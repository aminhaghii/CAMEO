"""
Tests for Phase 3 Findings remediation:
  Finding 3.2 — Operator warehouse mutate affordances hidden
  Finding 3.3 — Super-admin tenant-only nav links hidden
  Finding 3.4 — Last-admin lockout guard in admin routes
  Finding 3.1 — force-password-change uses Jinja template (not inline HTML)
  Finding 4.4 — Reactivity audit log warning is descriptive
"""

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app


# ══════════════════════════════════════════════════════════════
#  Finding 3.1 — Force-password-change uses Jinja template
# ══════════════════════════════════════════════════════════════

class TestForcePasswordChangeTemplate:

    def test_change_password_page_uses_render_template(self):
        """change_password_page must call render_template, not return a raw string."""
        from routes.auth import change_password_page
        source = inspect.getsource(change_password_page)
        assert 'render_template' in source, \
            "change_password_page should use render_template, not an inline HTML string"

    def test_change_password_page_no_inline_html(self):
        """change_password_page must not contain raw DOCTYPE or <html> tags."""
        from routes.auth import change_password_page
        source = inspect.getsource(change_password_page)
        assert '<!DOCTYPE' not in source, \
            "change_password_page should not have an inline DOCTYPE"
        assert '<html' not in source, \
            "change_password_page should not have inline <html> tags"

    def test_change_password_forced_template_exists(self):
        """change_password_forced.html template file must exist."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'change_password_forced.html'
        assert template_path.exists(), \
            f"Template {template_path} must exist"

    def test_change_password_forced_template_has_csrf(self):
        """Template must read the csrf_token cookie and send it in the header."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'change_password_forced.html'
        content = template_path.read_text(encoding='utf-8')
        assert 'csrf_token' in content, "Template must use the CSRF token"
        assert 'X-CSRF-Token' in content, "Template must send X-CSRF-Token header"

    def test_change_password_forced_template_has_logout_link(self):
        """Template must provide a logout escape hatch."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'change_password_forced.html'
        content = template_path.read_text(encoding='utf-8')
        assert '/auth/logout' in content, "Template must have a logout link as escape hatch"


# ══════════════════════════════════════════════════════════════
#  Finding 3.3 — Super-admin nav links hidden
# ══════════════════════════════════════════════════════════════

class TestSuperAdminNavLinks:

    def test_base_template_hides_inventory_from_super_admin(self):
        """base.html must conditionally hide Inventory link from super_admin."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'base.html'
        content = template_path.read_text(encoding='utf-8')
        # Look for the guard pattern around the inventory link
        assert "role != 'super_admin'" in content, \
            "base.html must guard tenant-only nav links from super_admin"

    def test_base_template_hides_warehouse_from_super_admin(self):
        """base.html must guard the Warehouse View link for super_admin."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'base.html'
        content = template_path.read_text(encoding='utf-8')
        # Count occurrences of the guard — should appear at least 3 times (Inventory, Matrix, Warehouse)
        guard_count = content.count("role != 'super_admin'")
        assert guard_count >= 3, \
            f"base.html should have at least 3 super_admin nav guards, found {guard_count}"


# ══════════════════════════════════════════════════════════════
#  Finding 3.2 — Operator warehouse controls hidden
# ══════════════════════════════════════════════════════════════

class TestOperatorWarehouseControls:

    def test_warehouse_has_can_mutate_getter(self):
        """warehouse.html must define a canMutate computed getter."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'warehouse.html'
        content = template_path.read_text(encoding='utf-8')
        assert 'canMutate' in content, \
            "warehouse.html must define canMutate computed getter"

    def test_warehouse_drag_gated_by_can_mutate(self):
        """Draggable attribute must be conditional on canMutate."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'warehouse.html'
        content = template_path.read_text(encoding='utf-8')
        assert 'canMutate ? \'true\' : \'false\'' in content or \
               'canMutate ? "true" : "false"' in content or \
               ':draggable="canMutate' in content, \
            "Chemical pool cards must make draggable conditional on canMutate"

    def test_warehouse_drop_zone_gated_by_can_mutate(self):
        """Drop handlers must be gated by canMutate."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'warehouse.html'
        content = template_path.read_text(encoding='utf-8')
        assert 'canMutate && handleDrop' in content, \
            "Section drop zones must gate @drop on canMutate"

    def test_warehouse_modal_actions_gated_by_can_mutate(self):
        """Move-back and delete buttons in section modal must be gated by canMutate."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'warehouse.html'
        content = template_path.read_text(encoding='utf-8')
        # The Actions block in the modal should be wrapped in x-if="canMutate"
        assert 'x-if="canMutate"' in content, \
            "Modal action buttons must be wrapped in x-if=\"canMutate\""

    def test_warehouse_can_mutate_only_for_admins(self):
        """canMutate must restrict to company_admin and super_admin only."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'warehouse.html'
        content = template_path.read_text(encoding='utf-8')
        assert "['company_admin', 'super_admin'].includes(this.userRole)" in content, \
            "canMutate must only allow company_admin and super_admin"


# ══════════════════════════════════════════════════════════════
#  Finding 3.4 — Last-admin lockout guard
# ══════════════════════════════════════════════════════════════

class TestLastAdminLockoutGuard:

    def test_suspend_user_has_last_admin_guard(self):
        """suspend_user must check that the company still has at least one admin."""
        from routes.admin import suspend_user
        source = inspect.getsource(suspend_user)
        assert 'remaining_admins' in source or 'last active administrator' in source, \
            "suspend_user must guard against suspending the last active admin"

    def test_approve_user_has_demotion_guard(self):
        """approve_user must check last-admin invariant when demoting a company_admin."""
        from routes.admin import approve_user
        source = inspect.getsource(approve_user)
        assert 'remaining_admins' in source or 'last active administrator' in source, \
            "approve_user must guard against demoting the last active admin"

    def test_suspend_user_last_admin_message(self):
        """suspend_user error message must be actionable."""
        from routes.admin import suspend_user
        source = inspect.getsource(suspend_user)
        assert 'last active administrator' in source or 'Promote another' in source, \
            "suspend_user error should tell user to promote another admin first"

    def test_suspend_self_still_blocked(self):
        """The existing self-suspend guard must remain in place."""
        from routes.admin import suspend_user
        source = inspect.getsource(suspend_user)
        assert 'You cannot suspend yourself' in source, \
            "Self-suspension guard must still be present"


# ══════════════════════════════════════════════════════════════
#  Finding 4.4 — Reactivity audit log warning
# ══════════════════════════════════════════════════════════════

class TestReactivityAuditLogWarning:

    def test_audit_log_warning_is_descriptive(self):
        """The warning for missing tenant context must explain fail-closed behaviour."""
        from logic.reactivity_engine import ReactivityEngine
        source = inspect.getsource(ReactivityEngine._save_audit_log)
        assert 'fail-closed' in source or 'no fallback' in source or 'intentionally skipped' in source, \
            "_save_audit_log warning must explain why the audit row is skipped (fail-closed)"

    def test_audit_log_does_not_fallback_to_shared_db(self):
        """_save_audit_log must NOT fall back to USER_DB_PATH when no tenant context."""
        from logic.reactivity_engine import ReactivityEngine
        source = inspect.getsource(ReactivityEngine._save_audit_log)
        assert 'USER_DB_PATH' not in source, \
            "_save_audit_log must not fall back to the shared USER_DB_PATH"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
