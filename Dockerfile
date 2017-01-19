FROM alpine:3.5
MAINTAINER Henning Jacobs <henning@jacobs1.de>

RUN apk add --no-cache python3 ca-certificates && \
    pip3 install --upgrade pip setuptools boto3 pykube && \
    rm -rf /var/cache/apk/* /root/.cache /tmp/* 

COPY autoscaler.py /
COPY scm-source.json /

ENTRYPOINT ["/autoscaler.py"]
