FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY --chmod=755 nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 index.html /usr/share/nginx/html/index.html
COPY --chmod=755 assets /usr/share/nginx/html/assets
