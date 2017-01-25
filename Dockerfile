FROM alpine:3.5
MAINTAINER Henning Jacobs <henning@jacobs1.de>

RUN apk add --no-cache python3 ca-certificates && \
    pip3 install --upgrade pip setuptools boto3 pykube && \
    rm -rf /var/cache/apk/* /root/.cache /tmp/* 

WORKDIR /

COPY kube_aws_autoscaler /kube_aws_autoscaler
COPY scm-source.json /

ENTRYPOINT ["python3", "-m", "kube_aws_autoscaler"]
