from app.streaming import encode_sse


def test_encode_sse_emits_a_complete_event_block():
    assert (
        encode_sse("status", {"message": "Searching"}) == 'event: status\ndata: {"message": "Searching"}\n\n'
    )
