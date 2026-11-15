# nextoomio base-files configuration (AC2, P0-F2.4)
# Enforces device hostname 'nextoomio' and standard local hosts mapping

hostname = "nextoomio"

do_install:append() {
    # Ensure hostname is nextoomio
    echo "nextoomio" > ${D}${sysconfdir}/hostname

    # Local host resolution mapping for nextoomio and nextoomio.local
    cat << 'EOF' > ${D}${sysconfdir}/hosts
127.0.0.1   localhost nextoomio nextoomio.local
::1         localhost ip6-localhost ip6-loopback nextoomio nextoomio.local
EOF
}
