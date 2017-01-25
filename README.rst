=================================
Kubernetes AWS Cluster Autoscaler
=================================

.. image:: https://travis-ci.org/hjacobs/kube-aws-autoscaler.svg?branch=master
   :target: https://travis-ci.org/hjacobs/kube-aws-autoscaler
   :alt: Travis CI Build Status

.. image:: https://coveralls.io/repos/github/hjacobs/kube-aws-autoscaler/badge.svg?branch=master
   :target: https://coveralls.io/github/hjacobs/kube-aws-autoscaler?branch=master
   :alt: Code Coverage

** THIS IS JUST A HACK - WORK IN PROGRESS **

Simple cluster autoscaler for AWS Auto Scaling Groups which sets the ``DesiredCapacity`` of one or more ASGs to the calculated number of nodes.

Goals:

* support multiple Auto Scaling Groups
* support resource buffer (overprovision fixed or percentage amount)
* respect Availability Zones, i.e. make sure that all AZs provide enough capacity
* be deterministic and predictable, i.e. the ``DesiredCapacity`` is only calculated based on the current cluster state
* require a minimum amount of configuration (preferably none)
* keep it simple

This hack was created as a proof of concept and born out of frustration with the `"official" cluster-autoscaler`_:

* it only scales up when "it's too late" (pods are unschedulable)
* it does not honor Availability Zones
* it does not support multiple Auto Scaling Groups
* it requires unnecessary configuration
* the code is quite complex


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
    Extra CPU requests to add to calculation, defaults to 1 (core).
``--buffer-memory-percentage``
    Extra memory requests to add to calculation, defaults to 200Mi.
``--buffer-pods-percentage``
    Extra number of pods to overprovision for, defaults to 10.
``--interval``
    Time to sleep between runs in seconds, defaults to 60 seconds.
``--once``
    Only run once and exit (useful for debugging).


.. _"official" cluster-autoscaler: https://github.com/kubernetes/contrib/tree/master/cluster-autoscaler
