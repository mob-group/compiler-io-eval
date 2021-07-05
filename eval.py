from evaluation import evaluate, generate
from reference_parser import load_reference, FunctionReference
from runner import create, Function
from typing import *
import os
import re


def test_hypothesis(ref: FunctionReference, impl: Function, impl_name: str, examples: str) -> bool:
    passes, tests, _ = evaluate(ref, impl, examples)

    print(f"{impl_name}: ({passes}/{tests}) {'OK' if passes == tests else 'NOT OK'}")
    return passes == tests


# def test_reference(ref_dir: os.DirEntry, impl_dir: Generator[os.DirEntry, None, None], num_examples: int) -> bool:
def test_reference(ref_dir: os.DirEntry, ref: FunctionReference, num_examples: int,
             impls: Generator[tuple[os.DirEntry, Function], None, None]) -> bool:
    try:
        example_gen = create(ref_dir.path)
        example_file = os.path.join(ref_dir.path, "examples")
        generate(ref, example_gen, num_examples, example_file)
    except Exception:
        raise Exception(f"could not create reference: {ref_dir.name}")

    print(f"testing {ref_dir.name}:")
    impl_passes = 0
    impl_tests = 0
    status = "NOT OK"
    for impl_file, impl in impls:
        passed = test_hypothesis(ref, impl, impl_file.name, example_file)

        impl_tests += 1
        if passed:
            impl_passes += 1
            status = "OK"

    if impl_tests == 0:
        raise Exception("no tests run")

    print(f"{ref_dir.name}: ({impl_passes}/{impl_tests}) {status}")
    return impl_passes > 0


def setup_impl(impl: os.DirEntry):
    backup_ext = ".bak"

    with open(impl, "r") as orig:
        contents = orig.read()

    if (m := re.match(r"\s*(\w+):", contents)) is None:
        raise Exception("could not find a function label")

    func = m[1]
    assert impl.name.startswith(func)

    with open(impl, "w") as new:
        new.write(f".globl {func}\n{contents}")

    with open(f"{impl}{backup_ext}", "w") as backup:
        backup.write(contents)


def fetch_impls(ref: os.DirEntry, impls: str):
    impl_dir = os.path.join(impls, ref.name)

    try:
        impl_file: os.DirEntry
        for impl_file in os.scandir(impl_dir):
            setup_impl(impl_file)
            yield impl_file
    except Exception:
        raise Exception(f"could not get implementations for {ref.name}")


def fetch(refs: str, impls: str):
    ref_dir: os.DirEntry
    for ref_dir in os.scandir(refs):
        yield ref_dir, fetch_impls(ref_dir, impls)


def test(refs_dir: str, impl_dir: str, num_examples: int):
    for ref_dir, ref in references(refs_dir):
        try:
            test_reference(ref_dir, ref, num_examples, implementations(ref_dir, impl_dir))
        except Exception:
            continue


def implementations(ref: os.DirEntry, impl_dir: str) -> Generator[tuple[os.DirEntry, Function], None, None]:
    if not os.path.isdir(impl_dir):
        return

    impl_file: os.DirEntry
    for impl_file in os.scandir(impl_dir):
        base: str
        ext: str
        base, ext = os.path.splitext(impl_file.name)

        if ext not in {".s", ".S"}:
            continue

        if not base.startswith(ref.name):
            continue

        lib_path = impl_file.path[:-1] + "so"
        try:
            impl = create(ref.path, impl_file.path, lib_path)
        except Exception:
            try:
                setup_impl(impl_file)
                impl = create(ref.path, impl_file.path, lib_path)
            except Exception:
                continue

        yield impl_file, impl


def references(refs_dir: str) -> Generator[tuple[os.DirEntry, FunctionReference], None, None]:
    if not os.path.isdir(refs_dir):
        return

    ref_dir: os.DirEntry
    for ref_dir in sorted(os.scandir(refs_dir), key=lambda x: (x.is_dir(), x.name)):
        if not os.path.isdir(ref_dir):
            continue

        try:
            ref = load_reference(ref_dir.path)
        except Exception:
            continue

        yield ref_dir, ref


if __name__ == '__main__':
    import argparse

    argparser = argparse.ArgumentParser()
    argparser.add_argument("references", help="path to references")
    argparser.add_argument("implementations", help="path to implementations")

    args = argparser.parse_args()

    sucesses = 0
    tests = 0
    # this is crazy slow
    # i think because it does all the work until it fails
    # e.g. to fail when there is no implementation it first needs to parse a reference
    # could make checking/failing out happen earlier
    # otherwise just leave it :)
    for ref_dir, ref in references(args.references):
        if "div" in ref_dir.name:
            continue

        impl_dir = os.path.join(args.implementations, ref_dir.name)
        try:
            if test_reference(ref_dir, ref, 50, implementations(ref_dir, impl_dir)):
                sucesses += 1

            tests += 1
        except Exception as e:
            # print(e)
            continue

    print(f"passed ({sucesses}/{tests})")
