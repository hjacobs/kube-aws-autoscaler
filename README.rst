=================================
Kubernetes AWS Cluster Autoscaler
=================================

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


.. _"official" cluster-autoscaler: https://github.com/kubernetes/contrib/tree/master/cluster-autoscaler
