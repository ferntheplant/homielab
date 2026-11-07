# The egress proxy for CI jobs: Squid, configured in squid.conf
FROM alpine:3.24
RUN apk add --no-cache squid
COPY squid.conf /etc/squid/squid.conf
USER squid
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
