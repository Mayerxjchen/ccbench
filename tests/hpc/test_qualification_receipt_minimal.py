"""Small retained tests for qualification receipt primitives (no old case data)."""
from bench.experiments.qualification_receipt import canonical_digest, parse_cp2k_output


def test_canonical_digest_is_order_independent_and_changes_on_tamper() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})


def test_cp2k_parser_requires_converged_energy() -> None:
    text = " SCF run converged in 5 steps\nENERGY| Total FORCE_EVAL ( QS ) energy [hartree] -7.123456\n"
    parsed = parse_cp2k_output(text)
    assert parsed["scf_converged"] is True
    assert float(parsed["energy_eh"]) == -7.123456
    tampered = parse_cp2k_output(text.replace("-7.123456", "-7.123457"))
    assert tampered["energy_eh"] != parsed["energy_eh"]
