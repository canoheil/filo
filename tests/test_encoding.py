from tools.check_encoding import stats


def test_encoding_stats_without_disclosing_content():
    result = stats("سلام 🌱 ????")
    assert result["arabic_script_characters"] == 4
    assert result["question_marks"] == 4
    assert result["replacement_characters"] == 0
    assert all(isinstance(value, int) for value in result.values())
