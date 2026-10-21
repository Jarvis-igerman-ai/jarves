"""V4-VC-12: the plan route builds BackendTrace(model=...) and the response also carries a model; finish() must not
pass `model` twice to the logger (it broke every ChatGPT-plan delegated turn after the answer arrived)."""
from jarves.brain import BackendTrace


def test_finish_with_constructor_model_and_response_model():
    t = BackendTrace("chatgpt", "CHATGPT_BACKEND", model="gpt-6.1-sol")
    t.finish("completed", {"model": "gpt-6.1-sol-2026", "service_tier": "default",
                           "usage": {"input_tokens": 5, "output_tokens": 3}})
    assert t.status == "completed"
