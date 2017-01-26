from unittest.mock import MagicMock

from kube_aws_autoscaler.main import parse_resource, apply_buffer, is_sufficient, calculate_usage_by_asg_zone, calculate_required_auto_scaling_group_sizes, get_nodes_by_asg_zone


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


def test_get_nodes_by_asg_zone():
    autoscaling = MagicMock()
    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': []}
    assert get_nodes_by_asg_zone(autoscaling, {}) == {}

    autoscaling.describe_auto_scaling_instances.return_value = {'AutoScalingInstances': [{'InstanceId': 'i-1', 'AutoScalingGroupName': 'myasg', 'AvailabilityZone': 'myaz'}]}
    assert get_nodes_by_asg_zone(autoscaling, {'foo': {'instance_id': 'i-1'}}) == {('myasg', 'myaz'): [{'asg_name': 'myasg', 'instance_id': 'i-1'}]}
