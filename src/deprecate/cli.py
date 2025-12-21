"""CLI entry point for pyDeprecate validation."""
import argparse
import os
import sys
from typing import List, Optional

from deprecate.utils import find_deprecated_callables


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="pyDeprecate CLI - Validate use of deprecated functions."
    )
    parser.add_argument(
        "path",
        type=str,
        nargs="?",
        default=".",
        help="Path to the module or package to scan (default: current directory)",
    )
    parser.add_argument(
        "--ignore",
        type=str,
        nargs="+",
        default=[],
        help="List of files or directories to ignore",
    )
    parser.add_argument(
        "--skip-errors",
        action="store_true",
        help="Do not exit with error code even if issues are found",
    )
    return parser.parse_args(args)


def main(args: Optional[List[str]] = None) -> int:
    """Run the CLI application."""
    parsed_args = parse_args(args)
    
    print(f"Scanning path: {parsed_args.path} ...")
    
    # Add current directory to sys.path to allow importing local modules
    sys.path.append(os.getcwd())
    
    try:
        if os.path.isdir(parsed_args.path):
            # It's a directory
            # First, try to see if it's a package (has __init__.py)
            if os.path.exists(os.path.join(parsed_args.path, "__init__.py")):
                # It is a package, use standard logic which handles recursion
                module_name = os.path.basename(os.path.abspath(parsed_args.path))
                deprecated_callables = find_deprecated_callables(module_name)
            else:
                # It's just a folder of scripts/modules (namespace-like or simple folder)
                deprecated_callables = []
                for root, _, files in os.walk(parsed_args.path):
                    for file in files:
                        if file.endswith(".py") and not file.startswith("__"):
                            # Construct module name from file path relative to CWD
                            rel_path = os.path.relpath(os.path.join(root, file), os.getcwd())
                            module_name = rel_path.replace(os.path.sep, ".")[:-3]  # remove .py
                            try:
                                deprecated_callables.extend(find_deprecated_callables(module_name, recursive=False))
                            except Exception as e:
                                # Start warning if individual file fails but continue
                                print(f"[WARNING] Could not scan {module_name}: {e}")
        else:
            # It's a file or module name
            deprecated_callables = find_deprecated_callables(parsed_args.path)
    except Exception as e:
        print(f"Error scanning {parsed_args.path}: {e}")
        return 1

    if not deprecated_callables:
        print("No deprecated callables found.")
        return 0

    issues_found = False
    
    # Process results
    invalid_args = [r for r in deprecated_callables if r.invalid_args]
    empty_mappings = [r for r in deprecated_callables if r.empty_mapping]
    identity_mappings = [r for r in deprecated_callables if r.identity_mapping]
    self_refs = [r for r in deprecated_callables if r.self_reference]
    no_effect = [r for r in deprecated_callables if r.no_effect]

    if invalid_args:
        print("\n[ERROR] Found functions with invalid argument mappings:")
        for r in invalid_args:
             print(f"  - {r.module}.{r.function}: {r.invalid_args}")
        issues_found = True

    if identity_mappings:
        print("\n[WARNING] Found functions with identity argument mappings (arg -> arg):")
        for r in identity_mappings:
            print(f"  - {r.module}.{r.function}: {r.identity_mapping}")
        issues_found = True
        
    if no_effect:
        print("\n[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):")
        for r in no_effect:
            print(f"  - {r.module}.{r.function}")
            if r.empty_mapping:
                print("    Reason: Empty mapping")
            if r.self_reference:
                print("    Reason: Self reference")
            if r.identity_mapping and not r.invalid_args:
                print("    Reason: All identity mappings")
        issues_found = True

    if issues_found:
        print("\nIssues were found in deprecated wrappers.")
        if not parsed_args.skip_errors and invalid_args:
             return 1
    else:
        print("\nAll deprecated wrappers look correct!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
