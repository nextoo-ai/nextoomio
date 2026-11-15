# nextoomio A/B slot A root partition specification (D-004, ARCHITECTURE.md section 3.6)
# Reference root partition by partition label (PARTLABEL=root_a) rather than fixed device node
CMDLINE_ROOT_PARTITION ?= "PARTLABEL=root_a"
