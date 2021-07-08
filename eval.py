import os
import re
from typing import *

from evaluation import generate, Evaluator, Result
from examples import ExampleInstance
from reference_parser import load_reference, FunctionReference, ParseError, UnsupportedTypeError
from runner import create_from, Function, CompilationError


class InvalidImplementationError(Exception):
    pass


ReferenceFile = tuple[FunctionReference, os.DirEntry]
ImplementationFile = tuple[Function, os.DirEntry]


def setup_impl(impl: os.DirEntry):
    backup_ext = ".bak"

    with open(impl, "r") as orig:
        contents = orig.read()

    if (m := re.match(r"\s*(\w+):", contents)) is None:
        raise InvalidImplementationError("could not find a function label")

    func = m[1]
    assert impl.name.startswith(func)

    with open(impl, "w") as new:
        new.write(f".globl {func}\n{contents}")

    with open(f"{impl}{backup_ext}", "w") as backup:
        backup.write(contents)


def implementations(basedir: str, impl: str, exts: set[str]) -> list[os.DirEntry]:
    d = os.path.join(basedir, impl)
    try:
        f: os.DirEntry
        return [f for f in os.scandir(d) if os.path.splitext(f.name)[1] in exts]
    except FileNotFoundError:
        return []
    except NotADirectoryError:
        return []


def references(refdir: str, impldir: str, impl_exts: set[str]) -> Generator[
    tuple[os.DirEntry, list[os.DirEntry]], None, None]:
    ref: os.DirEntry
    for ref in os.scandir(refdir):
        props, ref_c = os.path.join(ref.path, "props"), os.path.join(ref.path, "ref.c")

        if not (os.path.isfile(props) and os.path.isfile(ref_c)):
            continue

        if "div" in ref.name:
            continue

        if not (impls := implementations(impldir, ref.name, impl_exts)):
            continue

        yield ref, impls


def load_implementation(reference: FunctionReference, path_to_implementation: os.DirEntry) -> Function:
    try:
        return create_from(reference, path_to_implementation.path)
    except CompilationError:
        setup_impl(path_to_implementation)
        return create_from(reference, path_to_implementation.path)


def fetch(refdir: str, impldir: str, num_examples: int, impl_exts: set[str]) -> Generator[
    tuple[ReferenceFile, list[ExampleInstance], list[ImplementationFile]], None, None]:
    for ref_dir, impl_files in references(refdir, impldir, impl_exts):
        print(f"working on {ref_dir.name}")
        try:
            ref = load_reference(ref_dir.path)
            ref_impl = create_from(ref, os.path.join(ref_dir.path, "ref.c"))

            example_file = os.path.join(ref_dir.path, "examples")
            examples = generate(ref, ref_impl, num_examples, example_file)

            impls = [(load_implementation(ref, impl_file), impl_file) for impl_file in impl_files]

            if impls:
                yield (ref, ref_dir), examples, impls
        except ParseError:
            continue
        except CompilationError:
            continue
        except UnsupportedTypeError:
            continue


class ReferenceResult:
    def __init__(self, name: str, results: list[Result]):
        self.name = name
        self.results = results

    def passed(self) -> bool:
        return any(result.passed() for result in self.results)

    def is_trivial(self) -> bool:
        return all(result.is_trivial() for result in self.results)

    def __str__(self):
        status = "OK" if self.passed() else "NOT OK"
        passes = sum(1 for result in self.results if result.passed())

        return f"{self.name}: passed {passes}/{len(self.results)} tests ({status})"

    def full(self):
        results = "\n".join(" -> " + str(result) for result in self.results)
        return self.name + "\n" + results + "\n" + str(self)

    @staticmethod
    def partition(results: list) -> dict:
        partitions = {"pass": [], "fail": [], "trivial": []}

        for result in results:
            part = "fail" if not result.passed() else "trivial" if result.is_trivial() else "pass"
            partitions[part].append(result)

        return partitions

    @staticmethod
    def gen_report(results: list, verbose: bool, partitioned: bool = True) -> str:
        def stringify_many(items: Iterable) -> str:
            return "\n".join(item.full() if verbose else str(item) for item in items)

        if partitioned:
            partition = ReferenceResult.partition(results)

            return "*** PASSES ***\n\n" + stringify_many(partition["pass"]) \
                   + "\n\n*** FAILS ***\n\n" + stringify_many(partition["fail"]) \
                   + "\n\n*** TRIVIAL ***\n\n" + stringify_many(partition["trivial"])
        else:
            return stringify_many(results)

    @staticmethod
    def summary(results: list) -> str:
        passes = sum(1 for result in results if result.passed())

        return f"{passes}/{len(results)} successful implementations"


def test_implementation(ref: FunctionReference, implementation: ImplementationFile,
                        examples: list[ExampleInstance]) -> Result:
    impl, impl_file = implementation

    evaluator = Evaluator(ref, impl)
    result = evaluator.check(examples)
    result.name = impl_file.name

    return result


def test_reference(reference: ReferenceFile, impls: list[ImplementationFile],
                   examples: list[ExampleInstance]) -> ReferenceResult:
    ref, ref_dir = reference
    return ReferenceResult(ref_dir.name, [test_implementation(ref, impl, examples) for impl in impls])


def test(refdir: str, impldir: str, num_examples: int, impl_exts) -> list[ReferenceResult]:
    return [test_reference(reference, impls, examples)
            for reference, examples, impls in fetch(refdir, impldir, num_examples, impl_exts)]


if __name__ == '__main__':
    import argparse

    argparser = argparse.ArgumentParser()
    argparser.add_argument("references", help="path to references")
    argparser.add_argument("implementations", help="path to implementations")

    args = argparser.parse_args()

    results = test(args.references, args.implementations, 50, impl_exts={".c"})
    print(ReferenceResult.gen_report(results, True, partitioned=True))
