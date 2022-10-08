import os
import re
from datetime import datetime
from textwrap import indent, dedent
from typing import *
from typing import Dict, List, Tuple, Set
import random

import lumberjack
from evaluation import generate, Evaluator, Result, write_examples
from examples import ExampleInstance
from helper_types import *
from reference_parser import load_reference, FunctionReference
from runner import create_from, Function
from metrics import Metrics

ReferenceFile = Tuple[FunctionReference, os.DirEntry]
ImplementationFile = Tuple[Function, os.DirEntry]


def setup_impl(impl: os.DirEntry, mod_to_eval: str) -> str:
    """
    Fixes an assembly file produced by the compiler

    Currently this just adds a ".global" directive.
    Stores the new file in a temporary directory

    Throws an InvalidImplementationError if the function label can not be found.

    :param impl: the file to fix
    :param mod_to_eval: Modality to eval, s or c
    :return: the path to the fixed file
    """
    with open(impl, "r") as orig:
        contents = orig.read()

    if mod_to_eval == 's':
        # if (m := re.match(r"\s*(\w+):", contents)) is None:
        m = re.findall(r"\s*(\w+):", contents)
        if len(m) == 0:
            raise InvalidImplementationError("could not find a function label")

        # func = m[1]
        func = m[0]
        if not impl.name.startswith(func):
            lumberjack.getLogger("error").warning(f"function name ({func}) differs from implementation name ({impl.name})")

        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir)

        tmp_impl = os.path.join(tmp_dir, impl.name)
        with open(tmp_impl, "w") as new:
            new.write(f".global {func}\n{contents}")

    elif mod_to_eval == 'c':
        # TODO: check different name in signature function name and change it?
        tmp_impl = os.path.join(tmp_dir, impl.name)
        with open(tmp_impl, "w") as new:
            new.write(contents)

    return tmp_impl


def implementations(basedir: str, func: str, exts: Set[str]) -> List[os.DirEntry]:
    """
    Retrieves all (potential) implementations from a directory

    :param basedir: the base directory containing the implementation directory
    :param func: the directory containing the implementations
    :param exts: valid file extensions to include
    :return: the implementations contained in the directory
    """
    d = os.path.join(basedir, func)
    try:
        f: os.DirEntry
        return [f for f in sorted(os.scandir(d), key=lambda x: (x.is_file(), x.name)) if
                os.path.splitext(f.name)[1] in exts]
    except FileNotFoundError:
        return []
    except NotADirectoryError:
        return []


def references(refdir: str, impldir: str, impl_exts: Set[str]) -> Generator[
    Tuple[os.DirEntry, List[os.DirEntry]], None, None]:
    """
    Retrieves all (potential) function references

    Skips all directories which could not be a reference, and references without implementations.

    :param refdir: the directory containing all function references
    :param impldir: the directory containing the implementations
    :param impl_exts: the valid file extensions for an implementation
    :return: a generator over (reference directory, [implementations])
    """
    ref: os.DirEntry
    for ref in sorted(os.scandir(refdir), key=lambda x: (x.is_dir(), x.name)):
        props, ref_c = os.path.join(ref.path, "props"), os.path.join(ref.path, "ref.c")

        if not (os.path.isfile(props) and os.path.isfile(ref_c)):
            continue

        if not (impls := implementations(impldir, ref.name, impl_exts)):
            lumberjack.getLogger("error").warning(f"no implementation files found for reference {ref.name}")
            continue

        yield ref, impls


def load_implementation(reference: FunctionReference, path_to_implementation: os.DirEntry,
                        mod_to_eval) -> Optional[Function]:
    """
    Builds an executable function from a specific implementation

    This will try to fix an implementation if it can.

    :param reference: the reference to use for the implementation
    :param path_to_implementation: the implementation to compile and build from
    :param mod_to_eval: Modality to eval (s or c)
    :return: the resulting function, if it could be built
    """
    lib_name, _ = os.path.splitext(os.path.basename(path_to_implementation))
    lib_path = os.path.join(tmp_dir, f"{lib_name}.so")
    try:
        return create_from(reference, path_to_implementation.path, lib_path=lib_path)
    except (CompilationError, AttributeError) as e:
        lumberjack.getLogger("error").error(f"raw file: {str(e)}")

    try:
        new_path = setup_impl(path_to_implementation, mod_to_eval=mod_to_eval)
        # for some reason it breaks if the retry compiles into the same file
        return create_from(reference, new_path, lib_path=lib_path[:-3] + "-retry.so")
    except (CompilationError, AttributeError) as e:
        lumberjack.getLogger("error").error(f"backup file: {str(e)}")

    return None


def fetch(refdir: str, impldir: str, mod_to_eval: str) -> Generator[
    Tuple[ReferenceFile, List[ImplementationFile]], None, None]:
    """
    Retrieves and builds all references and their implementations

    Skips:
      - invalid references
      - invalid implementations
      - references with no valid implementations

    :param refdir: the directory containing all references
    :param impldir: the directory containing all implementations
    :param mod_to_eval: Modality to evaluate (c or s)
    :return: a generator over references and their implementations, preserving file information
    """
    impl_exts: Set[str] = assembly_files if mod_to_eval == 's' else c_files  # :param impl_exts: the valid file extensions for an implementation
    for ref_dir, impl_files in references(refdir, impldir, impl_exts):
        print(f"working on {ref_dir.name}")
        try:
            ref = load_reference(ref_dir.path)
        except Exception as e:
            lumberjack.getLogger("error").error(str(e))  # TODO: Here is where we get WRONG REFS
            print(e)
            continue

        impls = []
        for impl_file in impl_files:
            try:
                impl = load_implementation(ref, impl_file, mod_to_eval)
                if impl is None:
                    continue
                impls.append((impl, impl_file))
            except (AttributeError, CompilationError, UnsupportedTypeError, OSError, InvalidImplementationError) as e:
                print("uh oh----------------")
                lumberjack.getLogger("error").error(str(e))
        print(f"impls: {len(impls)}")
        if impls:
            yield (ref, ref_dir), impls
        else:
            lumberjack.getLogger("error").warning(f"no valid implementations in {impldir}")
            yield (ref, ref_dir), []


from dataclasses import dataclass

@dataclass
class ReportDict:
    passes: List
    fails: List
    trivials: List
    total_passes: int
    total_results: int



class ReferenceResult:
    """
    A collection of results for a reference with multiple implementations
    """

    def __init__(self, name: str, results: List[Result]):
        self.name = name
        self.results = results

    def passed(self) -> bool:
        """
        :return: :code:`True` if any implementations passed all their tests
        """
        return any(result.passed() for result in self.results)

    def is_trivial(self) -> bool:
        """
        :return: :code:`True` if all implementations were tested trivially
        """
        return all(result.is_trivial() for result in self.results)

    def __str__(self):
        status = "OK" if self.passed() else "NOT OK"
        passes = sum(1 for result in self.results if result.passed())
        metrics = Metrics.reduce([r.metrics for r in self.results if r.passed()])
        if not metrics:
            metrics = 'NONE (TESTS NOT PASSED)'

        return f"{self.name}: passed {passes}/{len(self.results)} tests ({status}) | REDUCED_METRICS: {metrics}"

    def full(self, show_failures: bool) -> str:
        """
        Formats a description of the full results

        :param show_failures: set to :code:`True` to display failures if they occur
        :return: the formatted description
        """
        return dedent('''\
        {name}
        {results}
        {summ}\
        ''').format(name=self.name,
                    results="\n".join(indent(result.full(show_failures), "  ") for result in self.results),
                    summ=self)

    @staticmethod
    def partition(results: list) -> dict:
        """
        Splits a bunch of reference results up

        :param results: the results to split
        :return: the results split into {"pass": .., "fail": .., "trivial": ..}
        """
        partitions = {"pass": [], "fail": [], "trivial": []}

        for result in results:
            part = "fail" if not result.passed() else "trivial" if result.is_trivial() else "pass"
            partitions[part].append(result)

        return partitions

    @staticmethod
    def gen_report(results: list, verbose: bool, show_failures: bool = True, partitioned: bool = True) -> str:
        """
        Formats a nice description of the results from many reference tests

        :param results: the reference results to display
        :param verbose: whether to show full information on each test
        :param show_failures: whether to show failed tests cases for each test
        :param partitioned: set to :code:`True` to split the report up into passes, fails, and trivial cases
        :return: the formatted report of all tests
        """

        def stringify_many(items: Iterable) -> str:
            # assert verbose is not False and show_failures is not True  # just a meaningless use case
            assert not (verbose and not show_failures)

            return "\n".join(item.full(show_failures) if verbose else str(item) for item in items)

        if partitioned:
            partition = ReferenceResult.partition(results)
            passes = partition["pass"]
            fails = partition["fail"]
            trivials = partition["trivial"]

            res = dedent('''\
            *** PASSES {n_pass}/{tests} ***

            {passes}

            *** FAILS {n_fail}/{tests} ***

            {fails}

            *** TRIVIAL {n_trivial}/{tests} ***

            {trivials}\
            ''').format(passes=stringify_many(passes),
                        fails=stringify_many(fails),
                        trivials=stringify_many(trivials),
                        n_pass=len(passes),
                        n_fail=len(fails),
                        n_trivial=len(trivials),
                        tests=len(results))
        else:
            res = stringify_many(results)

        return dedent('''\
        {res}

        {summ}\
        ''').format(res=res, summ=ReferenceResult.summary(results))

    @staticmethod
    def gen_report_json(results: list, partitioned: bool = True) -> Dict:
        """
        Formats a nice description of the results from many reference tests -> JSON

        :param results: the reference results to display
        :param verbose: whether to show full information on each test
        :param show_failures: whether to show failed tests cases for each test
        :param partitioned: set to :code:`True` to split the report up into passes, fails, and trivial cases
        :return: the DICT report of all tests
        """

        # TODO: # like gen_report, but returning a nice dict/json


        if partitioned:
            partition = ReferenceResult.partition(results)
            passes = partition["pass"]
            fails = partition["fail"]
            trivials = partition["trivial"]
            total_passes = sum(1 for result in results if result.passed())
            total_results = len(results)
            from dataclasses import asdict
            return asdict(ReportDict(passes=passes, fails=fails, trivials=trivials, total_passes=total_passes, total_results=total_results))


        else:
            assert RuntimeError('partitioned=False not implemented for dict')


    @staticmethod
    def summary(results: list) -> str:
        """
        :param results: a bunch of reference results
        :return: a formatted description of how many results were passes
        """
        passes = sum(1 for result in results if result.passed())

        return f"{passes}/{len(results)} successful implementations"


def test_implementation(ref: FunctionReference, implementation: ImplementationFile,
                        examples: List[ExampleInstance]) -> Result:
    """
    Tests a single implementation on a collection of examples

    :param ref: the function reference to use for the test
    :param implementation: the function implementation to use for the test
    :param examples: the examples to test the the implementation on
    :return: the result of the test
    """
    impl, impl_file = implementation

    evaluator = Evaluator(ref, impl)
    result = evaluator.check(examples)
    result.name = impl_file.name

    result.metrics = Metrics.create_metrics(ref, implementation)

    return result


def test_reference(reference: ReferenceFile, impls: List[ImplementationFile],
                   examples: List[ExampleInstance]) -> ReferenceResult:
    """
    Tests a singular reference, with many implementations, on a collection of examples

    :param reference: the function reference to use for the test
    :param impls: the function implementations to use for the test
    :param examples: the examples to test the the implementations on
    :return: the result of the test
    """
    ref, ref_dir = reference
    return ReferenceResult(ref_dir.name, [test_implementation(ref, impl, examples) for impl in impls])


def set_seed(seed: int):
    random.seed(seed)


def test(refdir: str, impldir: str, num_examples: int, mod_to_eval, seed, arch) -> List[ReferenceResult]:
    """
    Tests all implementations and their corresponding references on examples

    Generates examples for each reference from its "ref.c".

    :param refdir: the directory containing all references
    :param impldir: the directory containing all implementations
    :param num_examples: the number of example to (attempt to) generate for each reference
    :param mod_to_eval: The modality to evaluate (s, c, io).
    :param seed: random seed
    :param arch: Architecture (x86 or arm)
    """
    assert arch in ['x86', 'arm']
    if arch == 'arm':
        raise NotImplemented(arch)
    assert mod_to_eval in ['s', 'c']

    lumberjack.getLogger("general").info(f"testing beginning: {datetime.now():%d/%m/%Y %H:%M:%S}")

    set_seed(seed)

    results = []
    for reference, impls in fetch(refdir, impldir, mod_to_eval):
        ref, ref_dir = reference
        if len(impls) == 0:
            results.append(ReferenceResult(ref_dir.name, []))
            continue
        try:
            ref_impl = create_from(ref, os.path.join(ref_dir.path, "ref.c"))

            example_file = os.path.join(ref_dir.path, "examples")
            examples = generate(ref, ref_impl, num_examples)
            write_examples(ref, examples, example_file)

            results.append(test_reference(reference, impls, examples))
        except Exception as e:
            lumberjack.getLogger("error").error(str(e))
            # results.append(ReferenceResult(ref_dir.name, []))  # TODO: check if at least 1 was ok (didn't crash)

    return results


if __name__ == '__main__':
    import argparse

    argparser = argparse.ArgumentParser()
    argparser.add_argument("references", help="path to references")
    argparser.add_argument("implementations", help="path to implementations")
    argparser.add_argument("--seed", type=int, default=0)
    argparser.add_argument("--n", type=int, default=10, help="Number of IO tests per function")
    argparser.add_argument("--arch", type=str, default='x86', help="Architecture")
    argparser.add_argument("--mod-to-eval", type=str, default='s', help="Options: s, c")

    args = argparser.parse_args()

    results = test(args.references, args.implementations, args.n, mod_to_eval=args.mod_to_eval, seed=args.seed,
                   arch=args.arch)
    print(ReferenceResult.gen_report(results, True, partitioned=True))
    #results = ReferenceResult.gen_report_json(results, partitioned=True)
    #print(results)
