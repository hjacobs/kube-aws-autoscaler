=================================
Kubernetes AWS Cluster Autoscaler
=================================

.. image:: https://travis-ci.org/hjacobs/kube-aws-autoscaler.svg?branch=master
   :target: https://travis-ci.org/hjacobs/kube-aws-autoscaler
   :alt: Travis CI Build Status

.. image:: https://coveralls.io/repos/github/hjacobs/kube-aws-autoscaler/badge.svg?branch=master;_=1
   :target: https://coveralls.io/github/hjacobs/kube-aws-autoscaler?branch=master
   :alt: Code Coverage

Simple cluster autoscaler for AWS Auto Scaling Groups which sets the ``DesiredCapacity`` of one or more ASGs to the calculated number of nodes.

Goals:

* support multiple Auto Scaling Groups
* support resource buffer (overprovision fixed or percentage amount)
* respect Availability Zones, i.e. make sure that all AZs provide enough capacity
* be deterministic and predictable, i.e. the ``DesiredCapacity`` is only calculated based on the current cluster state
* scale down slowly to mitigate service disruptions, i.e. at most one node at a time
* support "elastic" workloads like daily up/down scaling
* support AWS Spot Fleet (not yet implemented)
* require a minimum amount of configuration (preferably none)
* keep it simple

This autoscaler was initially created as a proof of concept and born out of frustration with the `"official" cluster-autoscaler`_:

* it only scales up when "it's too late" (pods are unschedulable)
* it does not honor Availability Zones
* it does not support multiple Auto Scaling Groups
* it requires unnecessary configuration
* the code is quite complex

Disclaimer
==========

**Use at your own risk!**
This autoscaler was only tested with Kubernetes versions 1.5.2 to 1.7.7.
There is no guarantee that it works in previous Kubernetes versions.

**Is it production ready?**
Yes, the ``kube-aws-autoscaler`` is running in production at Zalando for months, see https://github.com/zalando-incubator/kubernetes-on-aws for more information and deployment configuration.

How it works
============

The autoscaler consists of a simple main loop which calls the ``autoscale`` function every 60 seconds (configurable via the ``--interval`` option).
The main loop keeps no state (like history), all input for the ``autoscale`` function comes from either static configuration or the Kubernetes API server.
The ``autoscale`` function performs the following task:

* retrieve the list of all (worker) nodes from the Kubernetes API and group them by Auto Scaling Group (ASG) and Availability Zone (AZ)
* retrieve the list of all pods from the Kubernetes API
* calculate the current resource "usage" for every ASG and AZ by summing up all pod resource requests (CPU, memory and number of pods)
* calculates the currently required number of nodes per AWS Auto Scaling Group:

  * iterate through every ASG/AZ combination
  * use the calculated resource usage (sum of resource requests) and add the resource requests of any unassigned pods (pods not scheduled on any node yet)
  * apply the configured buffer values (10% extra for CPU and memory by default)
  * find the `allocatable capacity`_ of the weakest node
  * calculate the number of required nodes by adding up the capacity of the weakest node until the sum is greater than or equal to requested+buffer for both CPU and memory
  * sum up the number of required nodes from all AZ for the ASG

* adjust the number of required nodes if it would scale down more than one node at a time
* set the ``DesiredCapacity`` for each ASG to the calculated number of required nodes

The whole process relies on having properly configured resource requests for all pods.


Usage
=====

Create the necessary IAM role (to be used by ``kube2iam`` if you have it deployed):

* Modify ``deploy/cloudformation.yaml`` and change the AWS account ID and the worker node's role name as necessary.
* Create the Cloud Formation stack from ``deploy/cloudformation.yaml``.

Deploy the autoscaler to your running cluster:

.. code-block:: bash

    $ kubectl apply -f deploy/deployment.yaml

See below for optional configuration parameters.


Configuration
=============

The following command line options are supported:

``--buffer-cpu-percentage``
    Extra CPU requests % to add to calculation, defaults to 10%.
``--buffer-memory-percentage``
    Extra memory requests % to add to calculation, defaults to 10%.
``--buffer-pods-percentage``
    Extra pods requests % to add to calculation, defaults to 10%.
``--buffer-cpu-fixed``
    Extra CPU requests to add to calculation, defaults to 200m.
``--buffer-memory-fixed``
    Extra memory requests to add to calculation, defaults to 200Mi.
``--buffer-pods-fixed``
    Extra number of pods to overprovision for, defaults to 10.
``--buffer-spare-nodes``
    Number of extra "spare" nodes to provision per ASG/AZ, defaults to 1.
``--include-master-nodes``
    Do not ignore auto scaling group with master nodes.
``--interval``
    Time to sleep between runs in seconds, defaults to 60 seconds.
``--once``
    Only run once and exit (useful for debugging).
``--scale-down-step-fixed``
    Scale down step in terms of node count, defaults to 1.
``--scale-down-step-percentage``
    Scale down step in terms of node percentage (1.0 is 100%), defaults to 0%


.. _"official" cluster-autoscaler: https://github.com/kubernetes/autoscaler
.. _allocatable capacity: https://github.com/kubernetes/community/blob/master/contributors/design-proposals/node/node-allocatable.md
