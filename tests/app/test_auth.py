from app.web import auth

def test_hash_verifies_only_the_right_password():
    stored = auth.hash_password("abcd-efgh-jkmn")
    assert stored.startswith("scrypt$") and "abcd" not in stored
    assert auth.verify_password(" ABCD-efgh-jkmn ", stored)
    assert not auth.verify_password("abcd-efgh-jkmp", stored)
    assert not auth.verify_password("abcd-efgh-jkmn", "garbage")

def test_hashes_are_salted():
    assert auth.hash_password("same") != auth.hash_password("same")

def test_passwords_avoid_lookalike_characters():
    for _ in range(50):
        password = auth.new_password()
        assert len(password) == 14 and password.count("-") == 2
        assert not set(password) & set("01ilo")

def test_session_changes_with_the_password():
    first = auth.session_value(auth.hash_password("a"), "token")
    second = auth.session_value(auth.hash_password("a"), "token")
    assert first != second
