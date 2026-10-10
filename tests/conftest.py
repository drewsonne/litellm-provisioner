"""Let controller modules import without a live LiteLLM."""

from __future__ import annotations

import os

os.environ.setdefault("LITELLM_URL", "http://litellm.test:4000")
os.environ.setdefault("LITELLM_MASTER_KEY", "sk-master-test")
