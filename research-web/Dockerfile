FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
COPY --chmod=755 nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=755 index.html /usr/share/nginx/html/index.html
COPY --chmod=755 assets /usr/share/nginx/html/assets
COPY --chmod=755 console /usr/share/nginx/html/console
# SC-SCI-02: the Workspace origin the Research Console may call (https only; empty keeps the console same-origin only).
ARG RESEARCH_CONSOLE_WORKSPACE_ORIGIN=
RUN printf '%s' "$RESEARCH_CONSOLE_WORKSPACE_ORIGIN" | grep -Eqx '(https://[A-Za-z0-9.-]+(:[0-9]{1,5})?)?' \
    && sed -i "s#__WORKSPACE_ORIGIN__#$RESEARCH_CONSOLE_WORKSPACE_ORIGIN#" /etc/nginx/conf.d/default.conf \
    && ! grep -q __WORKSPACE_ORIGIN__ /etc/nginx/conf.d/default.conf
