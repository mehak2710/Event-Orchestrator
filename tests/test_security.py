from app.security import sign, verify_signature

SECRET = "s3cret"
BODY = b'{"id":"1","type":"order.created"}'


def test_valid_signature_passes():
    assert verify_signature(BODY, sign(BODY, SECRET), SECRET)


def test_wrong_secret_fails():
    assert not verify_signature(BODY, sign(BODY, "other"), SECRET)


def test_tampered_body_fails():
    assert not verify_signature(BODY + b" ", sign(BODY, SECRET), SECRET)


def test_missing_header_fails():
    assert not verify_signature(BODY, None, SECRET)