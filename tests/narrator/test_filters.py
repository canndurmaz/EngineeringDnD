from narrator.filters import clean


def test_plain_prose_passes_through():
    text = "The bracket holds. The team exhales."
    assert clean(text, {"7"}) == text


def test_invented_die_roll_sentence_is_dropped():
    text = "You roll a natural 20. The bracket holds."
    assert clean(text, {"7"}) == "The bracket holds."


def test_dc_claim_is_dropped():
    text = "That beats DC 13 easily. The harness is rerouted."
    assert clean(text, {"7"}) == "The harness is rerouted."


def test_d20_mention_is_dropped():
    assert clean("The d20 clatters. It works.", {"7"}) == "It works."


def test_number_matching_a_committed_fact_is_kept():
    text = "Severity drops by 7."
    assert clean(text, {"7"}) == text


def test_number_contradicting_the_facts_is_dropped():
    assert clean("Severity drops by 42.", {"7"}) == ""


def test_output_is_capped_at_four_sentences():
    text = " ".join(f"Sentence {w}." for w in
                    ["one", "two", "three", "four", "five", "six"])
    assert clean(text, set()).count(".") == 4


def test_assistant_preamble_is_stripped():
    text = "Sure! Here is the narration: The bracket holds."
    assert clean(text, set()) == "The bracket holds."


def test_wrapping_quotes_are_stripped():
    assert clean('"The bracket holds."', set()) == "The bracket holds."


def test_empty_input_returns_empty():
    assert clean("", set()) == ""


def test_all_sentences_rejected_returns_empty():
    assert clean("You rolled 19. DC was 12.", set()) == ""


def test_year_like_numbers_are_allowed_through():
    # Four-digit numbers read as dates or part numbers, not game state.
    assert clean("The 2019 edition of the standard applies.", set()) != ""
