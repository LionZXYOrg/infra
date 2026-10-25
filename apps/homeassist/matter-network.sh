#!/usr/bin/env bash
# Run in the host network namespace before starting Matter Server.
set -euo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# == 1 || ( $# == 2 && $2 == --router ) ]] || fail 'Pass the LAN interface name and optional --router flag.'
router=false
[[ $# != 2 ]] || router=true
interface=$1
[[ $interface =~ ^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,14}$ && $interface != all && $interface != default ]] \
  || fail 'Invalid LAN interface name.'
root=${MATTER_IPV6_CONF_ROOT:-/proc/sys/net/ipv6/conf}
directory=$root/$interface
[[ -d $directory ]] || fail 'The LAN interface has no IPv6 configuration.'
for name in disable_ipv6 accept_ra accept_ra_rt_info_max_plen; do
  [[ -r $directory/$name ]] || fail 'The IPv6 configuration is incomplete.'
done
[[ $(<"$directory/disable_ipv6") == 0 ]] || fail 'IPv6 is disabled on the LAN interface.'
accept_ra=$(<"$directory/accept_ra")
maximum=$(<"$directory/accept_ra_rt_info_max_plen")
[[ $accept_ra =~ ^[012]$ ]] || fail 'Invalid router announcement configuration.'
[[ $maximum =~ ^(-1|0|[1-9][0-9]{0,2})$ && $maximum -le 128 ]] \
  || fail 'Invalid route prefix limit.'
[[ -w $directory/accept_ra && -w $directory/accept_ra_rt_info_max_plen ]] \
  || fail 'The IPv6 configuration is not writable. Run this command as root.'
if [[ $router == true ]]; then
  [[ -r $root/all/forwarding && -w $root/all/forwarding ]] || fail 'IPv6 forwarding configuration is unavailable.'
  forwarding=$(<"$root/all/forwarding")
  [[ $forwarding =~ ^[01]$ ]] || fail 'Invalid IPv6 forwarding configuration.'
fi
# Linux defaults to rejecting the /64 routes advertised by Thread border routers.
# https://www.kernel.org/doc/html/latest/networking/ip-sysctl.html
if [[ $accept_ra != 2 ]]; then printf '2\n' > "$directory/accept_ra"; fi
if (( maximum < 64 )); then printf '64\n' > "$directory/accept_ra_rt_info_max_plen"; fi
if [[ $router == true && $forwarding != 1 ]]; then printf '1\n' > "$root/all/forwarding"; fi
printf 'IPv6 route learning is ready on %s.\n' "$interface"
