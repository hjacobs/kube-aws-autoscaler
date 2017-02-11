#!/usr/bin/env python3

import argparse
import collections
import itertools
import logging
import os
import re
import time

import boto3
import pykube

FACTORS = {
    'm': 1 / 1000,
    'K': 1000,
    'M': 1000**2,
    'G': 1000**3,
    'T': 1000**4,
    'P': 1000**5,
    'E': 1000**6,
    'Ki': 1024,
    'Mi': 1024**2,
    'Gi': 1024**3,
    'Ti': 1024**4,
    'Pi': 1024**5,
    'Ei': 1024**6
}

RESOURCE_PATTERN = re.compile('^(\d*)(\D*)$')

RESOURCES = ['cpu', 'memory', 'pods']
DEFAULT_CONTAINER_REQUESTS = {'cpu': '10m', 'memory': '50Mi'}
DEFAULT_BUFFER_PERCENTAGE = {'cpu': 10, 'memory': 10, 'pods': 10}
DEFAULT_BUFFER_FIXED = {'cpu': '200m', 'memory': '200Mi', 'pods': '10'}

logger = logging.getLogger('autoscaler')


STATS = {}


def parse_resource(v: str):
    '''Parse Kubernetes resource string'''
    match = RESOURCE_PATTERN.match(v)
    factor = FACTORS.get(match.group(2), 1)
    return int(match.group(1)) * factor


def get_node_capacity_tuple(node: dict):
    capacity = node['capacity']
    return tuple(capacity[resource] for resource in RESOURCES)


def apply_buffer(requested: dict, buffer_percentage: dict, buffer_fixed: dict):
    requested_with_buffer = {}
    for resource, val in requested.items():
        requested_with_buffer[resource] = val * (1. + buffer_percentage.get(resource, 0)/100) + buffer_fixed.get(resource, 0)
    return requested_with_buffer


def find_weakest_node(nodes):
    return sorted(nodes, key=get_node_capacity_tuple)[0]


def is_sufficient(requested: dict, capacity: dict):
    for resource, cap in capacity.items():
        if requested.get(resource, 0) > cap:
            return False
    return True


def is_node_ready(node):
    '''
    Return whether the given pykube Node has "Ready" status
    '''
    for condition in node.obj['status'].get('conditions', []):
        if condition['type'] == 'Ready' and condition['status'] == 'True':
            return True
    return False


def get_nodes(api) -> dict:
    nodes = {}
    for node in pykube.Node.objects(api):
        region = node.labels['failure-domain.beta.kubernetes.io/region']
        zone = node.labels['failure-domain.beta.kubernetes.io/zone']
        instance_type = node.labels['beta.kubernetes.io/instance-type']
        capacity = {}
        for key, val in node.obj['status']['capacity'].items():
            capacity[key] = parse_resource(val)
        instance_id = node.obj['spec']['externalID']
        obj = {'name': node.name,
               'region': region, 'zone': zone, 'instance_id': instance_id, 'instance_type': instance_type,
               'capacity': capacity,
               'ready': is_node_ready(node),
               'unschedulable': node.obj['spec'].get('unschedulable', False),
               'master': node.labels.get('master', 'false') == 'true'}
        nodes[node.name] = obj
    return nodes


def get_nodes_by_asg_zone(autoscaling, nodes: dict) -> dict:
    # first map instance_id to node object for later look up
    instances = {}
    for node in nodes.values():
        instances[node['instance_id']] = node

    nodes_by_asg_zone = collections.defaultdict(list)

    response = autoscaling.describe_auto_scaling_instances(InstanceIds=list(instances.keys()))
    for instance in response['AutoScalingInstances']:
        instances[instance['InstanceId']]['asg_name'] = instance['AutoScalingGroupName']
        key = instance['AutoScalingGroupName'], instance['AvailabilityZone']
        nodes_by_asg_zone[key].append(instances[instance['InstanceId']])
    return nodes_by_asg_zone


def calculate_usage_by_asg_zone(pods: list, nodes: dict) -> dict:
    usage_by_asg_zone = {}

    for pod in pods:
        phase = pod.obj['status'].get('phase')
        if phase == 'Succeeded':
            # ignore completed jobs
            continue
        node = nodes.get(pod.obj['spec'].get('nodeName'))
        if node:
            asg_name = node['asg_name']
            zone = node['zone']
        else:
            # pod is unassigned/pending
            asg_name = 'unknown'
            # TODO: we actually might know the AZ by looking at volumes..
            zone = 'unknown'
        requests = collections.defaultdict(int)
        requests['pods'] = 1
        for container in pod.obj['spec']['containers']:
            container_requests = container['resources'].get('requests', {})
            for resource in RESOURCES:
                if resource != 'pods':
                    value = container_requests.get(resource)
                    if not value:
                        logger.debug('Container {}/{} has no resource request for {}'.format(
                                     pod.name, container['name'], resource))
                        value = DEFAULT_CONTAINER_REQUESTS[resource]
                    requests[resource] += parse_resource(value)
        key = asg_name, zone
        if key not in usage_by_asg_zone:
            usage_by_asg_zone[key] = {resource: 0 for resource in RESOURCES}
        for resource in usage_by_asg_zone[key]:
            usage_by_asg_zone[key][resource] += requests.get(resource, 0)
    return usage_by_asg_zone


def format_resource(value: float, resource: str):
    if resource == 'cpu':
        return '{:.1f}'.format(value)
    elif resource == 'memory':
        return '{:.0f}Mi'.format(value / (1024*1024))
    elif resource == 'pods':
        return '{:.0f}'.format(value)
    return '{:.0f}'.format(value)


def slow_down_downscale(asg_sizes: dict, nodes_by_asg_zone: dict):
    node_counts_by_asg = collections.defaultdict(int)
    for key, nodes in sorted(nodes_by_asg_zone.items()):
        asg_name, zone = key
        node_counts_by_asg[asg_name] += len(nodes)

    for asg_name, desired_size in sorted(asg_sizes.items()):
        amount_of_downscale = node_counts_by_asg[asg_name] - desired_size
        if amount_of_downscale >= 2:
            new_desired_size = node_counts_by_asg[asg_name] - 1
            logger.info('Slowing down downscale: changing desired size of ASG {} from {} to {}'.format(asg_name, desired_size, new_desired_size))
            asg_sizes[asg_name] = new_desired_size

    return asg_sizes


def calculate_required_auto_scaling_group_sizes(nodes_by_asg_zone: dict, usage_by_asg_zone: dict, buffer_percentage: dict, buffer_fixed: dict):
    asg_size = collections.defaultdict(int)

    dump_info = STATS.get('last_info_dump', 0) < (time.time() - 600)

    for key, nodes in sorted(nodes_by_asg_zone.items()):
        asg_name, zone = key
        requested = usage_by_asg_zone.get(key) or {resource: 0 for resource in RESOURCES}
        pending = usage_by_asg_zone.get(('unknown', 'unknown'))
        if pending:
            # add requested resources from unassigned/pending pods
            for resource, val in pending.items():
                requested[resource] += val
        requested_with_buffer = apply_buffer(requested, buffer_percentage, buffer_fixed)
        weakest_node = find_weakest_node(nodes)
        required_nodes = 0
        capacity = {resource: 0 for resource in RESOURCES}
        while not is_sufficient(requested_with_buffer, capacity):
            for resource in capacity:
                capacity[resource] += weakest_node['capacity'][resource]
            required_nodes += 1

        for node in nodes:
            if node['unschedulable'] and not node['master']:
                logger.info('Node {} is marked as unschedulable, compensating.'.format(node['name']))
                required_nodes += 1

        overprovisioned = {resource: 0 for resource in RESOURCES}
        for resource, value in capacity.items():
            overprovisioned[resource] = value - requested[resource]

        if dump_info:
            logger.info('{}/{}:                {}'.format(asg_name, zone,
                        ' '.join([r.rjust(10).upper() for r in RESOURCES])))
            logger.info('{}/{}: requested:     {}'.format(asg_name, zone,
                        ' '.join([format_resource(requested[r], r).rjust(10) for r in RESOURCES])))
            logger.info('{}/{}: with buffer:   {}'.format(asg_name, zone,
                        ' '.join([format_resource(requested_with_buffer[r], r).rjust(10) for r in RESOURCES])))
            logger.info('{}/{}: weakest node:  {}'.format(asg_name, zone,
                        ' '.join([format_resource(weakest_node['capacity'][r], r).rjust(10) for r in RESOURCES])))
            logger.info('{}/{}: overprovision: {}'.format(asg_name, zone,
                        ' '.join([format_resource(overprovisioned[r], r).rjust(10) for r in RESOURCES])))
            logger.info('{}/{}: => {} nodes required (current: {})'.format(asg_name, zone, required_nodes, len(nodes)))
            STATS['last_info_dump'] = time.time()
        asg_size[asg_name] += required_nodes

    return asg_size


def scaling_activity_in_progress(autoscaling, asg_name: str):
    '''
    Return True if the given Auto Scaling Group currently has some activity in progress
    (e.g. replacing an instance, waiting for ELB draining or waiting for instance shut down)
    '''
    result = autoscaling.describe_scaling_activities(AutoScalingGroupName=asg_name, MaxRecords=20)
    for activity in result['Activities']:
        # "Progress" is a % value between 0 and 100 that indicates the progress of the activity.
        if activity['Progress'] < 100:
            return True
    return False


def resize_auto_scaling_groups(autoscaling, asg_size: dict, ready_nodes_by_asg: dict, dry_run: bool=False):
    asgs = {}
    response = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=list(asg_size.keys()))
    for asg in response['AutoScalingGroups']:
        asgs[asg['AutoScalingGroupName']] = asg

    for asg_name, desired_capacity in sorted(asg_size.items()):
        asg = asgs[asg_name]
        if desired_capacity > asg['MaxSize']:
            logger.warn('Desired capacity for ASG {} is {}, but exceeds max {}'.format(
                        asg_name, desired_capacity, asg['MaxSize']))
            desired_capacity = asg['MaxSize']
        elif desired_capacity < asg['MinSize']:
            logger.warn('Desired capacity for ASG {} is {}, but is lower than min {}'.format(
                        asg_name, desired_capacity, asg['MinSize']))
            desired_capacity = asg['MinSize']
        if desired_capacity < asg['DesiredCapacity']:
            # potential scale down, let's check if it is safe..
            if ready_nodes_by_asg.get(asg_name) < asg['DesiredCapacity']:
                logger.info('Some nodes are not ready in ASG {}, not scaling down from {} to {}'.format(
                            asg_name, asg['DesiredCapacity'], desired_capacity))
                desired_capacity = asg['DesiredCapacity']
            elif scaling_activity_in_progress(autoscaling, asg_name):
                logger.info('Scaling activity in progress for ASG {}, not scaling down from {} to {}'.format(
                            asg_name, asg['DesiredCapacity'], desired_capacity))
                desired_capacity = asg['DesiredCapacity']
        if desired_capacity != asg['DesiredCapacity']:
            logger.info('Changing desired capacity for ASG {} from {} to {}..'.format(
                        asg_name, asg['DesiredCapacity'], desired_capacity))
            if dry_run:
                logger.info('**DRY-RUN**: not performing any change')
            else:
                autoscaling.set_desired_capacity(AutoScalingGroupName=asg_name,
                                                 DesiredCapacity=desired_capacity)


def get_kube_api():
    try:
        config = pykube.KubeConfig.from_service_account()
    except FileNotFoundError:
        # local testing
        config = pykube.KubeConfig.from_file(os.path.expanduser('~/.kube/config'))
    api = pykube.HTTPClient(config)
    return api


def get_nodes_by_name(nodes: list):
    nodes_by_name = {}
    for node in nodes:
        nodes_by_name[node['name']] = node
    return nodes_by_name


def get_ready_nodes_by_asg(nodes_by_asg_zone):
    ready_nodes_by_asg = collections.defaultdict(int)
    for key, nodes in sorted(nodes_by_asg_zone.items()):
        asg_name, _ = key
        for node in nodes:
            if node['ready']:
                ready_nodes_by_asg[asg_name] += 1
    return ready_nodes_by_asg


def autoscale(buffer_percentage: dict, buffer_fixed: dict, dry_run: bool):
    api = get_kube_api()

    all_nodes = get_nodes(api)
    region = list(all_nodes.values())[0]['region']

    autoscaling = boto3.client('autoscaling', region)
    nodes_by_asg_zone = get_nodes_by_asg_zone(autoscaling, all_nodes)
    # we only consider nodes found in an ASG (old "ghost" nodes returned from Kubernetes API are ignored)
    nodes_by_name = get_nodes_by_name(itertools.chain(*nodes_by_asg_zone.values()))

    pods = pykube.Pod.objects(api, namespace=pykube.all)

    usage_by_asg_zone = calculate_usage_by_asg_zone(pods, nodes_by_name)
    asg_size = calculate_required_auto_scaling_group_sizes(nodes_by_asg_zone, usage_by_asg_zone, buffer_percentage, buffer_fixed)
    asg_size = slow_down_downscale(asg_size, nodes_by_asg_zone)
    ready_nodes_by_asg = get_ready_nodes_by_asg(nodes_by_asg_zone)
    resize_auto_scaling_groups(autoscaling, asg_size, ready_nodes_by_asg, dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', help='Dry run mode: do not change anything, just print what would be done',
                        action='store_true')
    parser.add_argument('--debug', '-d', help='Debug mode: print more information', action='store_true')
    parser.add_argument('--once', help='Run loop only once and exit', action='store_true')
    parser.add_argument('--interval', type=int, help='Loop interval', default=60)
    for resource in RESOURCES:
        parser.add_argument('--buffer-{}-percentage'.format(resource), type=float,
                            help='{} buffer %%'.format(resource.capitalize()), default=DEFAULT_BUFFER_PERCENTAGE[resource])
        parser.add_argument('--buffer-{}-fixed'.format(resource), type=str,
                            help='{} buffer (fixed amount)'.format(resource.capitalize()), default=DEFAULT_BUFFER_FIXED[resource])
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.getLogger('botocore').setLevel(logging.WARN)

    buffer_percentage = {}
    buffer_fixed = {}
    for resource in RESOURCES:
        buffer_percentage[resource] = getattr(args, 'buffer_{}_percentage'.format(resource))
        buffer_fixed[resource] = parse_resource(getattr(args, 'buffer_{}_fixed'.format(resource)))

    if args.dry_run:
        logger.info('**DRY-RUN**: no autoscaling will be performed!')

    while True:
        try:
            autoscale(buffer_percentage, buffer_fixed, dry_run=args.dry_run)
        except:
            logger.exception('Failed to autoscale')
        if args.once:
            return
        time.sleep(args.interval)
