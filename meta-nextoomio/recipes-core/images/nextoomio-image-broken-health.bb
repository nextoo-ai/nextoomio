require nextoomio-image.bb

SUMMARY = "nextoomio deliberately broken test image: failing local health check (P0-F4.7 AC2)"
DESCRIPTION = "Deliberately broken image variant that boots systemd successfully but deliberately fails the local health check per P0-F4.7 and ARCHITECTURE.md section 3.6."

IMAGE_INSTALL:append = " nextoomio-test-faults-health"
