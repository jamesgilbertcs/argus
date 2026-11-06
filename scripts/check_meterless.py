"""Explicit live Nightshift connectivity/model check; optional tiny inference."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

if __package__:
    from .reasoning_assessment import strict_json
    from .reasoning_providers import MeterlessConfig, MeterlessProvider, ProviderError
    from .reasoning_view import ReasoningError
    from .zetaris_client import ROOT
else:
    from reasoning_assessment import strict_json
    from reasoning_providers import MeterlessConfig, MeterlessProvider, ProviderError
    from reasoning_view import ReasoningError
    from zetaris_client import ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--infer", action="store_true", help="Also request one small authenticated JSON completion")
    args = parser.parse_args(argv)
    try:
        config = MeterlessConfig.from_environment(args.env_file)
        provider = MeterlessProvider(replace(config, max_output_tokens=256))
        if config.model not in provider.available_models():
            raise ProviderError("The configured METERLESS_MODEL is not advertised by the runtime")
        if args.infer:
            schema = {"type": "object", "properties": {"ok": {"type": "boolean", "enum": [True]}},
                      "required": ["ok"], "additionalProperties": False}
            reply = provider.generate(("Return only JSON matching the supplied schema.", "Return exactly an object with ok set to true."), schema)
            result = strict_json(reply.text)
            if type(result) is not dict or set(result) != {"ok"} or result["ok"] is not True:
                raise ProviderError("Small inference returned an unexpected result")
    except ReasoningError as error:
        print(f"Meterless check failed: {error}")
        return 1
    print(f"Meterless authenticated model discovery passed; model: {config.model}")
    if args.infer:
        print(f"Small real inference passed; finish_reason=stop; output_mode={MeterlessProvider.OUTPUT_MODE}")
    print("This checks connectivity and inference only; no security investigation was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
