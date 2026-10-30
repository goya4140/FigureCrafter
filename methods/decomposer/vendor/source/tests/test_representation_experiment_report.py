from tools.compare_representation_experiments import check_failure_count


def test_check_failure_count_handles_non_object_details():
    assert check_failure_count({"passed": True, "detail": []}) is None
    assert check_failure_count({"passed": False, "detail": {"failures": [{"code": "x"}]}}) == 1
