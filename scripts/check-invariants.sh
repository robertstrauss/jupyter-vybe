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

# T-SETTINGS: every settings group the frontend reads must exist in the
# schema. An accessor reading a group that is not there does not fail -- it
# returns the hard-coded default forever, which is the same silent-no-op class
# as the schema file that was named for the wrong plugin (docs/13 R14).
groups=$(grep -oE "this\.group<[^>]*>\('[a-zA-Z]+'\)" src/plugins/settings.ts \
  | grep -oE "'[a-zA-Z]+'" | tr -d "'" | sort -u)
missing=""
for group in $groups; do
  if ! grep -q "\"$group\": {" schema/settings.json; then
    missing="$missing $group"
  fi
done
if [ -n "$missing" ]; then
  echo "FAIL T-SETTINGS: settings.ts reads groups absent from the schema:$missing"
  echo "      those accessors silently return their defaults forever"
  fail=1
else
  echo "pass  T-SETTINGS: every settings group read is declared in the schema"
fi

# T-FRAMES: every frame op the server emits must be handled by the frontend.
# An unhandled op is a round that produces nothing and says nothing -- twice
# now (protocolError, resync), so it is a gate rather than a habit.
emitted=$(grep -ohE '"op": "[a-zA-Z]+"' jupyter_vybe/*.py \
  | grep -oE '"[a-zA-Z]+"$' | tr -d '"' | sort -u)
unhandled=""
for op in $emitted; do
  case "$op" in
    # Handled by the transport rather than the round: status drives the
    # connection state, done ends the stream, tool results round-trip.
    status|done) continue ;;
  esac
  if ! grep -q "'$op'" src/plugins/rounds.ts src/plugins/agentClient.ts; then
    unhandled="$unhandled $op"
  fi
done
if [ -n "$unhandled" ]; then
  echo "FAIL T-FRAMES: the server emits ops the frontend ignores:$unhandled"
  echo "      an ignored op is a round that does nothing and says nothing"
  fail=1
else
  echo "pass  T-FRAMES: every emitted frame op is handled"
fi

# T-RENDERERS: a settings field renderer must name a top-level schema
# property. addRenderer splits its id at the LAST dot, so an id like
# "jupyter-vybe:settings.agent.command" registers against a plugin called
# "jupyter-vybe:settings.agent" -- which does not exist, so the renderer is
# silently never used. Two were dead that way (docs/13 R14).
bad=""
for id in $(grep -oE 'addRenderer\(`\$\{SETTINGS_PLUGIN\}\.[a-zA-Z.]+`' \
    src/plugins/settingsRenderers.tsx 2>/dev/null \
    | sed -E 's/.*SETTINGS_PLUGIN\}\.//; s/`$//'); do
  case "$id" in
    *.*) bad="$bad $id (nested: only top-level properties can have renderers)" ;;
    *) grep -q "\"$id\": {" schema/settings.json || bad="$bad $id (not in schema)" ;;
  esac
done
if [ -n "$bad" ]; then
  echo "FAIL T-RENDERERS: settings renderers that can never fire:$bad"
  fail=1
else
  echo "pass  T-RENDERERS: every settings renderer names a real top-level property"
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
