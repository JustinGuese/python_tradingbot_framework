FROM gmag11/metatrader5_vnc:2.3

# Refresh WineHQ apt key and install pinned winehq-staging 11.0
RUN mkdir -pm755 /etc/apt/keyrings \
    && wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
    && apt-get update \
    && apt-get install --install-recommends -y \
        winehq-staging=11.0.0~bookworm-1 \
        wine-staging=11.0.0~bookworm-1 \
        wine-staging-amd64=11.0.0~bookworm-1 \
        wine-staging-i386=11.0.0~bookworm-1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

ENV PATH="/opt/wine-staging/bin:${PATH}" \
    WINEDLLOVERRIDES="mscoree,mshtml=d"

# Patch /Metatrader/start.sh at build time:
# - Linux: pip install ... mt5linux -> mt5linux==0.1.9
# - Windows: mt5linux>=0.1.9 -> rpyc==6.0.2 plumbum "numpy<2"
RUN sed -i 's/--no-deps mt5linux/--no-deps mt5linux==0.1.9/g' /Metatrader/start.sh \
    && sed -i 's/"mt5linux>=0.1.9"/rpyc==6.0.2 plumbum "numpy<2"/g' /Metatrader/start.sh \
    && grep -q 'mt5linux==0.1.9' /Metatrader/start.sh \
    && grep -q 'rpyc==6.0.2 plumbum "numpy<2"' /Metatrader/start.sh
