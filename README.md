# litellm-provisioner

Kubernetes operator that creates LiteLLM virtual keys from `LiteLLMKey`
resources and writes each key into a Secret in the declaring namespace. Only
LiteLLM itself needs the master key; each service declares the key it needs in
its own chart. Removing the resource revokes the key and deletes the Secret.

```yaml
apiVersion: litellm.drewsonne.github.io/v1
kind: LiteLLMKey
metadata:
  name: claude-memory
  namespace: claude-memory
spec:
  alias: claude-memory          # key_alias in LiteLLM; must be unique
  models: [claude-haiku-4-5, claude-opus-4-6]
  maxBudget: 25                 # US dollars
  budgetDuration: 7d
  secretName: litellm-key       # Secret in this namespace, field api_key
```

## Install

```bash
helm repo add litellm-provisioner https://drewsonne.github.io/litellm-provisioner
helm install litellm-provisioner litellm-provisioner/litellm-provisioner -n litellm
```

The master key is read from Secret `litellm-master-key` (field `password`) in
the release namespace.

## Safety

Every key it creates carries LiteLLM metadata `managed_by: litellm-provisioner`
and `owner: <namespace>/<name>`. It never updates or revokes a key without
both matching, so keys created by hand are never touched.
