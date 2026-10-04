"""Keep standalone SDK unit tests independent of remote pricing metadata."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
