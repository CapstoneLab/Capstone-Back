from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cd_uses_single_ubuntu_public_oauth_callback() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert "PUBLIC_BASE_URL: https://112.186.136.153" in workflow
    assert 'GITHUB_REDIRECT_URI="${PUBLIC_BASE_URL}/auth/github/callback"' in workflow
    assert 'FRONTEND_REDIRECT_URL="${PUBLIC_BASE_URL}/auth/success"' in workflow
    assert "http://${{ secrets.SERVER_HOST }}:8000" not in workflow


def test_cd_connects_backend_to_public_ingress_routes() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "cd.yml").read_text(
        encoding="utf-8"
    )

    assert 'NETWORK="capstone-internal"' in workflow
    assert '--network "${NETWORK}"' in workflow
    assert "--network-alias backend" in workflow
    assert "location = /docs" in workflow
    assert "location = /openapi.json" in workflow
    assert "location = /redoc" in workflow
    assert "location ^~ /auth/" in workflow
    assert "location ^~ /api/" in workflow
