from unittest.mock import MagicMock

from kube_aws_autoscaler.main import (apply_buffer,
                                      calculate_required_auto_scaling_group_sizes,
                                      calculate_usage_by_asg_zone, get_nodes,
                                      get_nodes_by_asg_zone, is_sufficient,
                                      parse_resource,
                                      resize_auto_scaling_groups)


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


def test_calculate_required_auto_scaling_group_sizes():
    assert calculate_required_auto_scaling_group_sizes({}, {}, {}, {}) == {}
    node = {'capacity': {'cpu': 1, 'memory': 1, 'pods': 1}}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {}, {}, {}) == {'a1': 0}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('a1', 'z1'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 1}
    assert calculate_required_auto_scaling_group_sizes({('a1', 'z1'): [node]}, {('unknown', 'unknown'): {'cpu': 1, 'memory': 1, 'pods': 1}}, {}, {}) == {'a1': 1}


def test_get_nodes_by_asg_zone():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': []}
    assert get_nodes_by_asg_zone(autoscaling, {}) == {}

    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': [{'InstanceId': 'i-1', 'AutoScalingGroupName': 'myasg', 'AvailabilityZone': 'myaz'}]}
    assert get_nodes_by_asg_zone(autoscaling, {'foo': {'instance_id': 'i-1'}}) == {('myasg', 'myaz'): [{'asg_name': 'myasg', 'instance_id': 'i-1'}]}


def test_resize_auto_scaling_groups_empty():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_groups.return_value = {'AutoScalingGroups': []}
    asg_size = {}
    resize_auto_scaling_groups(autoscaling, asg_size)
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
    resize_auto_scaling_groups(autoscaling, asg_size)
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
    resize_auto_scaling_groups(autoscaling, asg_size)
    autoscaling.set_desired_capacity.assert_not_called()

    asg_size = {'asg1': 1}
    resize_auto_scaling_groups(autoscaling, asg_size, dry_run=True)
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
    resize_auto_scaling_groups(autoscaling, asg_size)
    autoscaling.set_desired_capacity.assert_not_called()

    asg_size = {'asg1': 3}
    resize_auto_scaling_groups(autoscaling, asg_size)
    autoscaling.set_desired_capacity.assert_not_called()


def test_get_nodes(monkeypatch):
    node = MagicMock()
    node.name = 'n1'
    node.labels = {
        'failure-domain.beta.kubernetes.io/region': 'eu-north-1',
        'failure-domain.beta.kubernetes.io/zone': 'eu-north-1a',
        'beta.kubernetes.io/instance-type': 'x1.mega'
    }
    node.obj = {
        'status': {'capacity': {'cpu': '2', 'memory': '16Gi', 'pods': '10'}},
        'spec': {'externalID': 'i-123'}
    }

    objects = MagicMock()
    objects.return_value = [node]
    monkeypatch.setattr('pykube.Node.objects', objects)
    api = MagicMock()
    assert get_nodes(api) == {'n1': {'region': 'eu-north-1', 'zone': 'eu-north-1a', 'instance_id': 'i-123', 'instance_type': 'x1.mega',
        'capacity': {'cpu': 2, 'memory': 16*1024*1024*1024, 'pods': 10}}}
