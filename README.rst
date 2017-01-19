=================================
Kubernetes AWS Cluster Autoscaler
=================================

** WORK IN PROGRESS **

Simple cluster autoscaler for AWS Auto Scaling Groups which sets the ``DesiredCapacity`` of one or more ASGs to the calculated number of nodes.

Goals:

* support multiple Auto Scaling Groups
* support resource buffer (overprovision fixed or percentage amount)
* respect Availability Zones, i.e. make sure that all AZs provide enough capacity
* be deterministic and predictable, i.e. the ``DesiredCapacity`` is only calculated based on the current cluster state
* keep it simple
