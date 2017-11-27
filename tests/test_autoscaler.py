import os
from unittest.mock import MagicMock
import pytest
from kube_aws_autoscaler.main import (apply_buffer, autoscale,
                                      calculate_required_auto_scaling_group_sizes,
                                      calculate_usage_by_asg_zone, chunks,
                                      format_resource, get_kube_api, get_nodes,
                                      get_nodes_by_asg_zone, is_node_ready,
                                      is_sufficient, main, parse_resource,
                                      resize_auto_scaling_groups,
                                      scaling_activity_in_progress,
                                      slow_down_downscale, app)


def test_parse_resource():
    assert parse_resource('100Mi') == 100*1024*1024


def test_apply_buffer():
    assert apply_buffer({'foo': 1}, {}, {}) == {'foo': 1}
    assert apply_buffer({'foo': 1}, {'foo': 10}, {}) == {'foo': 1.1}
    assert apply_buffer({'foo': 1}, {'foo': 10}, {'foo': 0.01}) == {'foo': 1.11}


def test_is_sufficient():
    assert is_sufficient({}, {})
    assert is_sufficient({}, {'foo': 1})
    assert is_sufficient({'foo': 0.5}, {'foo': 1})
    assert is_sufficient({'foo': 1}, {'foo': 1})
    assert not is_sufficient({'foo': 1.1}, {'foo': 1})


def test_calculate_usage_by_asg_zone():
    assert calculate_usage_by_asg_zone([], {}) == {}

    pod = MagicMock()
    pod.obj = {'status': {}, 'spec': {'containers': []}}
    assert calculate_usage_by_asg_zone([pod], {}) == {('unknown', 'unknown'): {'cpu': 0, 'memory': 0, 'pods': 1}}

    pod = MagicMock()
    pod.obj = {'status': {'phase': 'Succeeded'}, 'spec': {'containers': []}}
    assert calculate_usage_by_asg_zone([pod], {}) == {}

    pod = MagicMock()
    pod.name = 'mypod'
    pod.obj = {'status': {}, 'spec': {'nodeName': 'foo', 'containers': [{'name': 'mycont', 'resources': {'requests': {'cpu': '1m'}}}]}}
    nodes = {'foo': {'asg_name': 'asg1', 'zone': 'z1'}}
    assert calculate_usage_by_asg_zone([pod], nodes) == {('asg1', 'z1'): {'cpu': 1/1000, 'memory': 52428800, 'pods': 1}}

    # pod is assigned to a node, but pending
    pod = MagicMock()
    pod.name = 'mypod'
    pod.obj = {'status': {'phase': 'Pending'}, 'spec': {'nodeName': 'foo', 'containers': [{'name': 'mycont', 'resources': {'requests': {'cpu': '1m'}}}]}}
    nodes = {}
    assert calculate_usage_by_asg_zone([pod], nodes) == {('unknown', 'unknown'): {'cpu': 1/1000, 'memory': 52428800, 'pods': 1}}

    # pod is a "ghost" --- returned by API but node no longer exists
    pod = MagicMock()
    pod.name = 'mypod'
    pod.obj = {'status': {'phase': 'Running'}, 'spec': {'nodeName': 'foo', 'containers': [{'name': 'mycont', 'resources': {'requests': {'cpu': '1m'}}}]}}
    nodes = {}
    assert calculate_usage_by_asg_zone([pod], nodes) == {}

    # failed pod, should be included in the calculations
    pod = MagicMock()
    pod.name = 'mypod'
    pod.obj = {'status': {'phase': 'Failed'}, 'spec': {'nodeName': 'foo', 'containers': [{'name': 'mycont', 'resources': {'requests': {'cpu': '1m'}}}]}}
    assert calculate_usage_by_asg_zone([pod], {}) == {('unknown', 'unknown'): {'cpu': 1/1000, 'memory': 52428800, 'pods': 1}}

    # failed pod that won't be restarted, should not be included in the calculations
    pod = MagicMock()
    pod.name = 'mypod'
    pod.obj = {'status': {'phase': 'Failed'}, 'spec': {'nodeName': 'foo', 'restartPolicy': 'Never', 'containers': [{'name': 'mycont', 'resources': {'requests': {'cpu': '1m'}}}]}}
    assert calculate_usage_by_asg_zone([pod], {}) == {}


def test_calculate_required_auto_scaling_group_sizes():
    assert calculate_required_auto_scaling_group_sizes({}, {}, {}, {}) == {}
    node = {'allocatable': {'cpu': 1, 'memory': 1, 'pods': 1}, 'unschedulable': False, 'master': False}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {}, {}, {}) == {'a1': 0}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('a1', 'z1'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 1}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('unknown', 'unknown'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 1}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {}, {}, {}, buffer_spare_nodes=2) == {'a1': 2}


def test_calculate_required_auto_scaling_group_sizes_no_scaledown():
    nodes = [{'allocatable': {'cpu': 1, 'memory': 1, 'pods': 1}, 'unschedulable': False, 'master': False},
             {'allocatable': {'cpu': 1, 'memory': 1, 'pods': 1}, 'unschedulable': False, 'master': False}]
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): nodes}, {}, {}, {}) == {'a1': 0}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): nodes}, {}, {}, {}, disable_scale_down=True) == {'a1': 2}


def test_calculate_required_auto_scaling_group_sizes_cordon():
    node = {'name': 'mynode', 'allocatable': {'cpu': 1, 'memory': 1, 'pods': 1}, 'unschedulable': True, 'master': False, 'asg_lifecycle_state': 'InService'}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {}, {}, {}) == {'a1': 1}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('a1', 'z1'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 2}


def test_calculate_required_auto_scaling_group_sizes_unschedulable_terminating():
    node = {'name': 'mynode', 'allocatable': {'cpu': 1, 'memory': 1, 'pods': 1}, 'unschedulable': True, 'master': False, 'asg_lifecycle_state': 'Terminating'}
    # do not compensate if the instance is terminating.. (it will probably be replaced by ASG)
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {}, {}, {}) == {'a1': 0}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('a1', 'z1'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 1}


def test_get_nodes_by_asg_zone():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': []}
    assert get_nodes_by_asg_zone(autoscaling, {}) == {}

    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': [
        {'InstanceId': 'i-1', 'AutoScalingGroupName': 'myasg', 'AvailabilityZone': 'myaz', 'LifecycleState': 'InService'}
    ]}
    expected_result = {('myasg', 'myaz'): [{'asg_name': 'myasg', 'instance_id': 'i-1', 'asg_lifecycle_state': 'InService'}]}
    assert get_nodes_by_asg_zone(autoscaling, {'foo': {'instance_id': 'i-1'}}) == expected_result


def test_get_nodes_by_asg_zone_chunked():

    def describe_auto_scaling_instances(InstanceIds):
        # AWS API only supports chunks of 50
        assert len(InstanceIds) <= 50
        return {'AutoScalingInstances': list([
            {'InstanceId': instance_id, 'AutoScalingGroupName': 'myasg', 'AvailabilityZone': 'myaz', 'LifecycleState': 'InService'} for instance_id in InstanceIds
        ])}

    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_instances = describe_auto_scaling_instances
    expected_result = {('myasg', 'myaz'): []}
    nodes = {}
    for i in range(51):
        nodes['node-{:02d}'.format(i)] = {'instance_id': 'i-{:02d}'.format(i)}
        expected_result[('myasg', 'myaz')].append({'asg_name': 'myasg', 'instance_id': 'i-{:02d}'.format(i), 'asg_lifecycle_state': 'InService'})
    actual_result = get_nodes_by_asg_zone(autoscaling, nodes)
    assert actual_result == expected_result


def test_resize_auto_scaling_groups_empty():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {'AutoScalingGroups': []}
    asg_size = {}
    resize_auto_scaling_groups(autoscaling, asg_size, {})
    autoscaling.set_desired_capacity.assert_not_called()


def test_resize_auto_scaling_groups_downscale():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 2,
            'MinSize': 1,
            'MaxSize': 10
        }]
    }
    asg_size = {'asg1': 1}
    ready_nodes = {'asg1': 2}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_called_with(AutoScalingGroupName='asg1', DesiredCapacity=1)


def test_resize_auto_scaling_groups_nochange():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 2,
            'MinSize': 1,
            'MaxSize': 10
        }]
    }
    asg_size = {'asg1': 2}
    ready_nodes = {'asg1': 2}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_not_called()

    asg_size = {'asg1': 1}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes, dry_run=True)
    autoscaling.set_desired_capacity.assert_not_called()


def test_resize_auto_scaling_groups_constraints():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 2,
            'MinSize': 2,
            'MaxSize': 2
        }]
    }
    asg_size = {'asg1': 1}
    ready_nodes = {'asg1': 2}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_not_called()

    asg_size = {'asg1': 3}
    ready_nodes = {'asg1': 2}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_not_called()


def test_resize_auto_scaling_groups_to_min_max():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 3,
            'MinSize': 2,
            'MaxSize': 10
        }]
    }
    asg_size = {'asg1': 1}
    ready_nodes = {'asg1': 3}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_called_with(AutoScalingGroupName='asg1', DesiredCapacity=2)

    asg_size = {'asg1': 18}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_called_with(AutoScalingGroupName='asg1', DesiredCapacity=10)


def test_resize_auto_scaling_groups_activity_in_progress(monkeypatch):
    monkeypatch.setattr('kube_aws_autoscaler.main.scaling_activity_in_progress', lambda a, b: True)
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 3,
            'MinSize': 2,
            'MaxSize': 10
        }]
    }
    asg_size = {'asg1': 2}
    ready_nodes = {'asg1': 3}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_not_called()


def test_resize_auto_scaling_groups_nodes_not_ready(monkeypatch):
    monkeypatch.setattr('kube_aws_autoscaler.main.scaling_activity_in_progress', lambda a, b: False)
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {
        'AutoScalingGroups': [{
            'AutoScalingGroupName': 'asg1',
            'DesiredCapacity': 3,
            'MinSize': 2,
            'MaxSize': 10
        }]
    }
    asg_size = {'asg1': 2}
    ready_nodes = {'asg1': 2}
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes)
    autoscaling.set_desired_capacity.assert_not_called()


def test_activity_in_progress():
    autoscaling = MagicMock()
    autoscaling.describe_scaling_activities.return_value = {
        'Activities': []
    }
    assert not scaling_activity_in_progress(autoscaling, 'my-asg')
    autoscaling.describe_scaling_activities.return_value = {
        'Activities': [{
            'Progress': 100
        }]
    }
    assert not scaling_activity_in_progress(autoscaling, 'my-asg')
    autoscaling.describe_scaling_activities.return_value = {
        'Activities': [
            {'Progress': 100},
            {'Progress': 67}
        ]
    }
    assert scaling_activity_in_progress(autoscaling, 'my-asg')


def test_get_nodes(monkeypatch):
    node = MagicMock()
    node.name = 'n1'
    node.labels = {
        'failure-domain.beta.kubernetes.io/region': 'eu-north-1',
        'failure-domain.beta.kubernetes.io/zone': 'eu-north-1a',
        'beta.kubernetes.io/instance-type': 'x1.mega'
    }
    node.obj = {
        'status': {'allocatable': {'cpu': '2', 'memory': '16Gi', 'pods': '10'}},
        'spec': {'externalID': 'i-123'}
    }

    master = MagicMock()
    master.name = 'master'
    master.labels = {
        'failure-domain.beta.kubernetes.io/region': 'eu-north-1',
        'failure-domain.beta.kubernetes.io/zone': 'eu-north-1a',
        'beta.kubernetes.io/instance-type': 'a1.small',
        'master': 'true'
    }
    master.obj = {
        'status': {'allocatable': {'cpu': '2', 'memory': '16Gi', 'pods': '10'}},
        'spec': {'externalID': 'i-456'}
    }

    objects = MagicMock()
    objects.return_value = [node, master]
    monkeypatch.setattr('pykube.Node.objects', objects)
    api = MagicMock()
    assert get_nodes(api) == {'n1': {
        'name': 'n1',
        'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-123', 'instance_type': 'x1.mega',
        'allocatable': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10},
        'ready': False,
        'unschedulable': False,
        'master': False}}

    assert get_nodes(api, include_master_nodes=True) == {'n1': {
        'name': 'n1',
        'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-123', 'instance_type': 'x1.mega',
        'allocatable': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10},
        'ready': False,
        'unschedulable': False,
        'master': False}, 'master': {
            'name': 'master',
            'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-456', 'instance_type': 'a1.small',
            'allocatable': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10},
            'ready': False,
            'unschedulable': False,
            'master': True
         }}


def test_get_kube_api(monkeypatch):
    kube_config = MagicMock()
    kube_config.from_service_account.side_effect = FileNotFoundError
    monkeypatch.setattr('pykube.KubeConfig', kube_config)
    monkeypatch.setattr('pykube.HTTPClient', MagicMock())
    get_kube_api()
    kube_config.from_file.assert_called_once_with(os.path.expanduser('~/.kube/config'))


def test_autoscale(monkeypatch):
    kube_config = MagicMock()
    get_nodes = MagicMock()
    get_nodes.return_value = {'n1': {
                'name': 'n1',
                'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-123', 'instance_type': 'x1.mega',
                'allocatable': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10},
                'ready': True,
                'unschedulable': False,
                'master': False}}
    get_pods = MagicMock()
    pod = MagicMock()
    pod.obj = {'status': {}, 'spec': {'nodeName': 'n1', 'containers': [{'name': 'c1', 'resources': {'requests': {'cpu': '4000m'}}}]}}
    get_pods.return_value = [pod]
    boto3_client = MagicMock()
    boto3_client.return_value.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': [
        {'InstanceId': 'i-123', 'AutoScalingGroupName': 'a1', 'AvailabilityZone': 'eu-north-1a', 'LifecycleState': 'InService'}
    ]}
    boto3_client.return_value.describe_auto_scaling_groups.return_value = {'AutoScalingGroups': [{'AutoScalingGroupName': 'a1', 'DesiredCapacity': 1, 'MinSize': 1, 'MaxSize': 10}]}
    monkeypatch.setattr('pykube.KubeConfig', kube_config)
    monkeypatch.setattr('pykube.HTTPClient', MagicMock())
    monkeypatch.setattr('pykube.Pod.objects', get_pods)
    monkeypatch.setattr('kube_aws_autoscaler.main.get_nodes', get_nodes)
    monkeypatch.setattr('boto3.client', boto3_client)

    buffer_percentage = {}
    buffer_fixed = {}
    autoscale(buffer_percentage, buffer_fixed, False)
    boto3_client.return_value.set_desired_capacity.assert_called_with(AutoScalingGroupName='a1', DesiredCapacity=2)


def test_autoscale_node_without_asg(monkeypatch):
    kube_config = MagicMock()
    get_nodes = MagicMock()
    get_nodes.return_value = {'n1': {
                'name': 'n1',
                'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-123', 'instance_type': 'x1.mega',
                'allocatable': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10},
                'ready': True,
                'unschedulable': False,
                'master': False}}
    get_pods = MagicMock()
    pod = MagicMock()
    pod.obj = {'status': {}, 'spec': {'nodeName': 'n1', 'containers': [{'name': 'c1', 'resources': {'requests': {'cpu': '1000m'}}}]}}
    get_pods.return_value = [pod]
    boto3_client = MagicMock()
    boto3_client.return_value.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': []}
    boto3_client.return_value.describe_auto_scaling_groups.return_value = {'AutoScalingGroups': [{'AutoScalingGroupName': 'a1', 'DesiredCapacity': 1, 'MinSize': 1, 'MaxSize': 10}]}
    monkeypatch.setattr('pykube.KubeConfig', kube_config)
    monkeypatch.setattr('pykube.HTTPClient', MagicMock())
    monkeypatch.setattr('pykube.Pod.objects', get_pods)
    monkeypatch.setattr('kube_aws_autoscaler.main.get_nodes', get_nodes)
    monkeypatch.setattr('boto3.client', boto3_client)

    buffer_percentage = {}
    buffer_fixed = {}
    autoscale(buffer_percentage, buffer_fixed, False)


def test_main(monkeypatch):
    autoscale = MagicMock()
    monkeypatch.setattr('kube_aws_autoscaler.main.autoscale', autoscale)
    monkeypatch.setattr('sys.argv', ['foo', '--once', '--dry-run'])
    main()
    autoscale.assert_called_once_with(
        {'memory': 10, 'pods': 10, 'cpu': 10},
        {'memory': 209715200, 'pods': 10, 'cpu': 0.2},
        buffer_spare_nodes=1, include_master_nodes=False, dry_run=True, disable_scale_down=False)

    autoscale.side_effect = ValueError

    monkeypatch.setattr('sys.argv', ['foo', '--dry-run'])
    monkeypatch.setattr('time.sleep', MagicMock(side_effect=Exception))
    with pytest.raises(Exception):
        main()


def test_format_resource():
    assert format_resource(1, 'cpu') == '1.0'
    assert format_resource(1024*1024, 'memory') == '1Mi'
    assert format_resource(1, 'pods') == '1'
    assert format_resource(1, 'foo') == '1'


def test_slow_down_downscale():
    assert slow_down_downscale({}, {}) == {}
    assert slow_down_downscale({'a1': 1}, {('a1', 'z1'): [{}]}) == {'a1': 1}
    # scale up
    assert slow_down_downscale({'a1': 2}, {('a1', 'z1'): [{}]}) == {'a1': 2}
    assert slow_down_downscale({'a1': 10}, {('a1', 'z1'): [{}]}) == {'a1': 10}
    # scale down
    assert slow_down_downscale({'a1': 1}, {('a1', 'z1'): [{}, {}]}) == {'a1': 1}
    assert slow_down_downscale({'a1': 1}, {('a1', 'z1'): [{}, {}, {}]}) == {'a1': 2}


def test_is_node_ready():
    node = MagicMock()
    node.obj = {'status': {}}
    assert not is_node_ready(node)
    node.obj = {'status': {'conditions': [{'type': 'Ready', 'status': 'False'}]}}
    assert not is_node_ready(node)
    node.obj = {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    assert is_node_ready(node)


def test_chunks():
    assert list(chunks([], 1)) == []
    assert list(chunks([1], 1)) == [[1]]
    assert list(chunks([1, 2], 1)) == [[1], [2]]
    assert list(chunks([1, 2, 3], 2)) == [[1, 2], [3]]


def test_start_health_endpoint():
    flask = app.test_client()
    flask.testing = True
    response = flask.get('/healthz')
    assert response.status_code == 503
