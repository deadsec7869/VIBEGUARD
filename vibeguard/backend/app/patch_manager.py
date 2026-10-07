import difflib
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from .models import StructuredPatch

class PatchManager:
    def __init__(self, workspace_dir: Path, backup_dir: Optional[Path] = None):
        self.workspace_dir = workspace_dir.resolve()
        self.backups: dict[Path, str] = {}
        self.temp_backup_dir: Optional[Path] = backup_dir.resolve() if backup_dir else None
        self._created_temp_dir: bool = False

    def _ensure_backup_dir(self) -> Path:
        if self.temp_backup_dir is None:
            self.temp_backup_dir = Path(tempfile.mkdtemp(prefix="vibeguard_backup_")).resolve()
            self._created_temp_dir = True
        else:
            self.temp_backup_dir.mkdir(parents=True, exist_ok=True)
        return self.temp_backup_dir

    def _safe_resolve(self, file_path: str) -> Path:
        p = (self.workspace_dir / file_path).resolve()
        try:
            p.relative_to(self.workspace_dir)
        except ValueError:
            raise PermissionError(f"Refusing to patch file outside workspace: {file_path}")
        return p

    def backup(self, file_path: str) -> Path:
        target = self._safe_resolve(file_path)
        if not target.exists():
            raise FileNotFoundError(f"Target file not found: {file_path}")
        
        if target not in self.backups:
            original_content = target.read_text(encoding="utf-8")
            self.backups[target] = original_content
            
            # Write backup to controlled temporary directory outside target project
            backup_root = self._ensure_backup_dir()
            rel = target.relative_to(self.workspace_dir)
            backup_file = backup_root / rel
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_text(original_content, encoding="utf-8")
            return backup_file
        return self.temp_backup_dir / target.relative_to(self.workspace_dir)

    def validate_patch(self, patch: StructuredPatch) -> None:
        target = self._safe_resolve(patch.file)
        if not target.exists():
            raise FileNotFoundError(f"Target file not found: {patch.file}")
        
        content = target.read_text(encoding="utf-8")
        if not patch.operations:
            raise ValueError("StructuredPatch contains no operations")

        for op in patch.operations:
            if op.type != "replace":
                raise ValueError(f"Unsupported patch operation type: {op.type}")
            if not op.old_text:
                raise ValueError("old_text cannot be empty")
            if op.old_text not in content:
                raise ValueError(f"Target text not found in {patch.file}")
            count = content.count(op.old_text)
            if count != op.expected_matches:
                raise ValueError(f"Patch ambiguity: '{op.old_text}' found {count} times (expected {op.expected_matches})")

    def apply_patch(self, patch: StructuredPatch) -> str:
        """
        Validates, backs up, and applies structured patch.
        Returns the unified diff string.
        """
        target = self._safe_resolve(patch.file)
        if not target.exists():
            raise FileNotFoundError(f"Target file not found: {patch.file}")
        
        # 1. Validate operations before making any change
        self.validate_patch(patch)

        # 2. Capture and backup original content to controlled temp dir
        self.backup(patch.file)
        original_content = self.backups[target]
        new_content = original_content
        
        # 3. Apply exact replacements
        for op in patch.operations:
            if op.type == "replace":
                new_content = new_content.replace(op.old_text, op.new_text)

        # 4. Generate diff
        rel_str = str(target.relative_to(self.workspace_dir)).replace("\\", "/")
        diff_lines = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel_str}",
            tofile=f"b/{rel_str}",
        )
        diff_str = "".join(diff_lines)

        # 5. Write modified content
        target.write_text(new_content, encoding="utf-8")
        return diff_str

    def rollback(self):
        """Restores original files and cleans up temporary backup directory."""
        for target, original_content in self.backups.items():
            target.write_text(original_content, encoding="utf-8")
        self.backups.clear()
        self.cleanup()

    def cleanup(self):
        """Deletes temporary backup directory."""
        if self._created_temp_dir and self.temp_backup_dir and self.temp_backup_dir.exists():
            try:
                shutil.rmtree(self.temp_backup_dir, ignore_errors=True)
            except Exception:
                pass
            self.temp_backup_dir = None
            self._created_temp_dir = False
