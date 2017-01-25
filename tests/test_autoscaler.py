from kube_aws_autoscaler.main import parse_resource


def test_parse_resource():
    assert parse_resource('100Mi') == 100*1024*1024
