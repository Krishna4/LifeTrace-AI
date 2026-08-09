from unittest.mock import patch, MagicMock
from src.backend.query.slm_router import route_query_slm, classify_query_rule_based


def test_classify_query_rule_based_sql():
    res = classify_query_rule_based("How much did I pay Alex?")
    assert res.target_engine in ["SQL", "HYBRID"]


def test_classify_query_rule_based_vector():
    res = classify_query_rule_based("Summarize the meeting notes about project timeline")
    assert res.target_engine in ["VECTOR", "HYBRID"]


def test_route_query_slm_mocked():
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": '{"target_engine": "SQL", "sql_query": "SELECT * FROM transactions WHERE entity_person = \'Alex\'", "rationale": "Financial spending query"}'
        }
        mock_post.return_value = mock_response

        res = route_query_slm("How much did I pay Alex?")
        assert res.target_engine == "SQL"
        assert res.sql_query is not None
