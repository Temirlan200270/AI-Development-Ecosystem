# test_gemini.py — smoke test: list models + same Gemini chain as Temir agents
import asyncio
import os
import sys

try:
    from temir.env_bootstrap import load_dotenv_if_available

    load_dotenv_if_available()
except ImportError:
    try:
        from dotenv import load_dotenv
        from pathlib import Path

        load_dotenv(Path(__file__).resolve().parent / ".env")
    except ImportError:
        pass


def main() -> None:
    import google.generativeai as genai

    print("Step 0: google.generativeai imported.")
    print("\n--- Gemini API smoke test ---")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("\nERROR [1]: GEMINI_API_KEY not set.")
        print("Add to .env (GEMINI_API_KEY=...) or: set GEMINI_API_KEY=...")
        sys.exit(1)

    print(
        "\nStep 1: API key present (prefix "
        + repr(api_key[:4])
        + ", suffix "
        + repr(api_key[-4:])
        + ").",
    )

    try:
        genai.configure(api_key=api_key)
        print("Step 2: genai.configure OK.")
    except Exception as e:
        print("\nERROR [2]:", e)
        sys.exit(1)

    try:
        print("\nListing models...")
        models = genai.list_models()
        available = [m.name for m in models]
        print("Found", len(available), "models (showing gemini*):")
        for name in available:
            if "gemini" in name.lower():
                print(" ", name)
    except Exception as e:
        print("\nWARN: list_models failed:", e)

    from temir.agents.gemini_chain import get_gemini_model_chain
    from temir.llm.kernel import get_llm_kernel

    chain = get_gemini_model_chain()
    print("\nModel chain (quota -> next):", chain)
    print("Calling LLMKernel.generate_gemini (joke prompt)...")

    async def _ask():
        return await get_llm_kernel().generate_gemini(
            "Tell me a very short joke about programming.",
            rate_limiter=None,
            role_hint="manual_test",
            task_id="",
        )

    try:
        gout = asyncio.run(_ask())
        if not gout.success:
            print("\nERROR:", gout.error)
            sys.exit(1)
        print("Step 3: OK. billing_model =", gout.billing_model, "latency_ms =", gout.latency_ms)
        print("\n---\n", gout.text, "\n---", sep="")
    except Exception as e:
        print("\nERROR during generate:", e)
        sys.exit(1)

    print("\n--- PASS ---")


if __name__ == "__main__":
    main()
