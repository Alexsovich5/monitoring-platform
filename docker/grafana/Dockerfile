FROM node:0.10

# Everything comes from docker/grafana/vendor/, filled on the host by
# "make grafana-vendor" over HTTPS: node 0.10's OpenSSL and CA list cannot
# be relied on against current GitHub and npm TLS chains, so nothing is
# fetched here.  The files are checked against the committed SHA256SUMS
# before they are unpacked or installed.
COPY SHA256SUMS /tmp/vendor/SHA256SUMS
COPY vendor/ /tmp/vendor/
RUN cd /tmp/vendor && sha256sum -c SHA256SUMS \
 && mkdir /grafana \
 && tar xzf /tmp/vendor/v1.9.1.tar.gz --strip-components=1 -C /grafana

# less 1.4.2 lists mime, request, mkdirp and ycssmin as optional
# dependencies; --no-optional keeps npm from resolving its open-ended
# "request >=2.12.0", and the three that lessc uses are installed from
# their tarballs.
WORKDIR /build
COPY package.json /build/package.json
RUN npm install --no-optional \
      /tmp/vendor/less-1.4.2.tgz /tmp/vendor/ycssmin-1.0.1.tgz \
      /tmp/vendor/mkdirp-0.3.5.tgz /tmp/vendor/mime-1.2.11.tgz \
 && ./node_modules/.bin/lessc --version

# The "less:src" and "concat:cssDark/cssLight" steps of Grafana's Gruntfile
# (tasks/options/less.js and concat.js), run without grunt.
WORKDIR /grafana/src
COPY build-css.sh /build/build-css.sh
RUN sh /build/build-css.sh /build/node_modules/.bin/lessc \
 && rm -rf /tmp/vendor

COPY config.js /grafana/src/config.js
EXPOSE 3000
CMD ["python", "-m", "SimpleHTTPServer", "3000"]
