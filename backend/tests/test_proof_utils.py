from kimina_client.proof_utils import parse_client_response


def test_parse_client_response_treats_message_payload_as_error() -> None:
    out = parse_client_response(
        {"custom_id": "x", "response": {"message": "runtime failure", "time": 0.1}}
    )
    assert out["has_error"] is True
    assert out["is_valid_no_sorry"] is False
    assert out["is_valid_with_sorry"] is False


def test_parse_client_response_treats_missing_response_as_error() -> None:
    out = parse_client_response({"custom_id": "x"})
    assert out["has_error"] is True
    assert out["is_valid_no_sorry"] is False
    assert out["is_valid_with_sorry"] is False


def test_parse_client_response_treats_empty_response_object_as_valid_like_sync() -> None:
    out = parse_client_response({"custom_id": "x", "response": {}})
    assert out["has_error"] is False
    assert out["is_valid_no_sorry"] is True
    assert out["is_valid_with_sorry"] is True


def test_parse_client_response_marks_sorry_invalid_without_accept_sorry() -> None:
    out = parse_client_response(
        {
            "custom_id": "x",
            "response": {
                "env": 0,
                "sorries": [
                    {
                        "pos": {"line": 1, "column": 0},
                        "endPos": {"line": 1, "column": 1},
                        "goal": "False",
                    }
                ],
            },
        }
    )
    assert out["has_error"] is False
    assert out["is_valid_no_sorry"] is False
    assert out["is_valid_with_sorry"] is True
