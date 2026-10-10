# Reviewed public upstream image; pin updates after rechecking its startup contract.
FROM ghcr.io/tashfeenahmed/freellmapi@sha256:b07acc3494341ae8c4efc2f4ecd9ee2a16eb5db16c88753ef800730b832d62d1
WORKDIR /app
COPY --chown=node:node integrations/freellmapi/gateway.mjs /app/shadow-gateway/gateway.mjs
# Upstream's entrypoint fixes persistent-volume ownership, then drops to node.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["node", "/app/shadow-gateway/gateway.mjs"]
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||10000)+'/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
