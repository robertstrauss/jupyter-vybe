#!/usr/bin/env bash
# CI-blocking invariant gates (docs/10). The interesting behaviour of this
# project is absence, so it gets explicit gates rather than feature tests.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

# T-KERNEL-1: the agent must never be able to reach an execute_request.
# Matches a call site, not a mention in prose.
if grep -rnE '\.requestExecute[[:space:]]*\(' src/ 2>/dev/null; then
  echo "FAIL T-KERNEL-1: requestExecute called in src/"
  fail=1
else
  echo "pass  T-KERNEL-1: no requestExecute call site in src/"
fi

# T-KERNEL-1b: kernel connections are reachable from exactly one file.
offenders=$(grep -rlE 'sessionContext|IKernelConnection' src/ 2>/dev/null | grep -v 'src/introspect.ts' || true)
if [ -n "$offenders" ]; then
  echo "FAIL T-KERNEL-1b: kernel access outside src/introspect.ts:"
  echo "$offenders"
  fail=1
else
  echo "pass  T-KERNEL-1b: kernel access confined to src/introspect.ts"
fi

# T-SCHEMA: the settings schema file is named after the plugin that loads it.
# JupyterLab keys a schema by <package>:<basename>, so a mismatch means
# registry.load() finds nothing and every setting sits silently at its default.
# That shipped for weeks; only an end-to-end run caught it.
plugin_id=$(grep -o "const PLUGIN_ID = '[^']*'" src/plugins/settings.ts | cut -d"'" -f2)
schema_name="${plugin_id#*:}.json"
package_name=$(grep -o '"name": "[^"]*"' package.json | head -1 | cut -d'"' -f4)
if [ ! -f "schema/$schema_name" ]; then
  echo "FAIL T-SCHEMA: settings.ts loads '$plugin_id' but schema/$schema_name does not exist:"
  ls schema/
  fail=1
elif [ "${plugin_id%%:*}" != "$package_name" ]; then
  echo "FAIL T-SCHEMA: plugin id '$plugin_id' does not start with package name '$package_name'"
  fail=1
else
  echo "pass  T-SCHEMA: schema/$schema_name matches plugin id $plugin_id"
fi

# T-MUTATE: only mutations.ts may write to the notebook model.
offenders=$(grep -rlE 'sharedModel\.(insertCell|deleteCell|setSource)' src/ 2>/dev/null \
  | grep -v 'src/notebook/mutations.ts' || true)
if [ -n "$offenders" ]; then
  echo "FAIL T-MUTATE: notebook writes outside src/notebook/mutations.ts:"
  echo "$offenders"
  fail=1
else
  echo "pass  T-MUTATE: notebook writes confined to mutations.ts"
fi

# T-KERNEL-2: the server extension must not import kernel machinery.
if grep -rnE '^\s*(import|from)\s+(jupyter_client|ipykernel|nbclient)' jupyter_vybe/ 2>/dev/null; then
  echo "FAIL T-KERNEL-2: kernel package imported by jupyter_vybe"
  fail=1
else
  echo "pass  T-KERNEL-2: no kernel packages imported by jupyter_vybe"
fi

# Adapters must stay host-agnostic so the v1.1 sidecar can reuse them (D2).
if grep -rnE '^\s*(import|from)\s+(tornado|jupyter_server)' jupyter_vybe/adapters/ 2>/dev/null; then
  echo "FAIL D2: adapters import server machinery; the sidecar could not reuse them"
  fail=1
else
  echo "pass  D2: adapters are host-agnostic"
fi

exit $fail
