import asyncio
import codecs
import json
from pathlib import Path
from typing import get_args

import click

from guidellm.backend import BackendType
from guidellm.benchmark import ProfileType, benchmark_generative_text
from guidellm.config import print_config
from guidellm.preprocess.dataset import ShortPromptStrategy, process_dataset
from guidellm.scheduler import StrategyType

STRATEGY_PROFILE_CHOICES = set(
    list(get_args(ProfileType)) + list(get_args(StrategyType))
)


def parse_json(ctx, param, value):  # noqa: ARG001
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as err:
        raise click.BadParameter(f"{param.name} must be a valid JSON string.") from err


def parse_number_str(ctx, param, value):  # noqa: ARG001
    if value is None:
        return None

    values = value.split(",") if "," in value else [value]

    try:
        return [float(val) for val in values]
    except ValueError as err:
        raise click.BadParameter(
            f"{param.name} must be a number or comma-separated list of numbers."
        ) from err


@click.group()
def cli():
    pass


@cli.command(
    help="Run a benchmark against a generative model using the specified arguments."
)
@click.option(
    "--target",
    required=True,
    type=str,
    help="The target path for the backend to run benchmarks against. For example, http://localhost:8000",
)
@click.option(
    "--backend-type",
    type=click.Choice(list(get_args(BackendType))),
    help=(
        "The type of backend to use to run requests against. Defaults to 'openai_http'."
        f" Supported types: {', '.join(get_args(BackendType))}"
    ),
    default="openai_http",
)
@click.option(
    "--backend-args",
    callback=parse_json,
    default=None,
    help=(
        "A JSON string containing any arguments to pass to the backend as a "
        "dict with **kwargs."
    ),
)
@click.option(
    "--model",
    default=None,
    type=str,
    help=(
        "The ID of the model to benchmark within the backend. "
        "If None provided (default), then it will use the first model available."
    ),
)
@click.option(
    "--processor",
    default=None,
    type=str,
    help=(
        "The processor or tokenizer to use to calculate token counts for statistics "
        "and synthetic data generation. If None provided (default), will load "
        "using the model arg, if needed."
    ),
)
@click.option(
    "--processor-args",
    default=None,
    callback=parse_json,
    help=(
        "A JSON string containing any arguments to pass to the processor constructor "
        "as a dict with **kwargs."
    ),
)
@click.option(
    "--data",
    required=True,
    type=str,
    help=(
        "The HuggingFace dataset ID, a path to a HuggingFace dataset, "
        "a path to a data file csv, json, jsonl, or txt, "
        "or a synthetic data config as a json or key=value string."
    ),
)
@click.option(
    "--data-args",
    callback=parse_json,
    help=(
        "A JSON string containing any arguments to pass to the dataset creation "
        "as a dict with **kwargs."
    ),
)
@click.option(
    "--data-sampler",
    default=None,
    type=click.Choice(["random"]),
    help=(
        "The data sampler type to use. 'random' will add a random shuffle on the data. "
        "Defaults to None"
    ),
)
@click.option(
    "--rate-type",
    required=True,
    type=click.Choice(STRATEGY_PROFILE_CHOICES),
    help=(
        "The type of benchmark to run. "
        f"Supported types {', '.join(STRATEGY_PROFILE_CHOICES)}. "
    ),
)
@click.option(
    "--rate",
    default=None,
    callback=parse_number_str,
    help=(
        "The rates to run the benchmark at. "
        "Can be a single number or a comma-separated list of numbers. "
        "For rate-type=sweep, this is the number of benchmarks it runs in the sweep. "
        "For rate-type=concurrent, this is the number of concurrent requests. "
        "For rate-type=async,constant,poisson, this is the rate requests per second. "
        "For rate-type=synchronous,throughput, this must not be set."
    ),
)
@click.option(
    "--max-seconds",
    type=float,
    help=(
        "The maximum number of seconds each benchmark can run for. "
        "If None, will run until max_requests or the data is exhausted."
    ),
)
@click.option(
    "--max-requests",
    type=int,
    help=(
        "The maximum number of requests each benchmark can run for. "
        "If None, will run until max_seconds or the data is exhausted."
    ),
)
@click.option(
    "--warmup-percent",
    type=float,
    default=None,
    help=(
        "The percent of the benchmark (based on max-seconds, max-requets, "
        "or lenth of dataset) to run as a warmup and not include in the final results. "
        "Defaults to None."
    ),
)
@click.option(
    "--cooldown-percent",
    type=float,
    help=(
        "The percent of the benchmark (based on max-seconds, max-requets, or lenth "
        "of dataset) to run as a cooldown and not include in the final results. "
        "Defaults to None."
    ),
)
@click.option(
    "--disable-progress",
    is_flag=True,
    help="Set this flag to disable progress updates to the console",
)
@click.option(
    "--display-scheduler-stats",
    is_flag=True,
    help="Set this flag to display stats for the processes running the benchmarks",
)
@click.option(
    "--disable-console-outputs",
    is_flag=True,
    help="Set this flag to disable console output",
)
@click.option(
    "--output-path",
    type=click.Path(),
    default=Path.cwd() / "benchmarks.json",
    help=(
        "The path to save the output to. If it is a directory, "
        "it will save benchmarks.json under it. "
        "Otherwise, json, yaml, or csv files are supported for output types "
        "which will be read from the extension for the file path."
    ),
)
@click.option(
    "--output-extras",
    callback=parse_json,
    help="A JSON string of extra data to save with the output benchmarks",
)
@click.option(
    "--output-sampling",
    type=int,
    help=(
        "The number of samples to save in the output file. "
        "If None (default), will save all samples."
    ),
    default=None,
)
@click.option(
    "--random-seed",
    default=42,
    type=int,
    help="The random seed to use for benchmarking to ensure reproducibility.",
)
def benchmark(
    target,
    backend_type,
    backend_args,
    model,
    processor,
    processor_args,
    data,
    data_args,
    data_sampler,
    rate_type,
    rate,
    max_seconds,
    max_requests,
    warmup_percent,
    cooldown_percent,
    disable_progress,
    display_scheduler_stats,
    disable_console_outputs,
    output_path,
    output_extras,
    output_sampling,
    random_seed,
):
    asyncio.run(
        benchmark_generative_text(
            target=target,
            backend_type=backend_type,
            backend_args=backend_args,
            model=model,
            processor=processor,
            processor_args=processor_args,
            data=data,
            data_args=data_args,
            data_sampler=data_sampler,
            rate_type=rate_type,
            rate=rate,
            max_seconds=max_seconds,
            max_requests=max_requests,
            warmup_percent=warmup_percent,
            cooldown_percent=cooldown_percent,
            show_progress=not disable_progress,
            show_progress_scheduler_stats=display_scheduler_stats,
            output_console=not disable_console_outputs,
            output_path=output_path,
            output_extras=output_extras,
            output_sampling=output_sampling,
            random_seed=random_seed,
        )
    )


def decode_escaped_str(_ctx, _param, value):
    """
    Click auto adds characters. For example, when using --pad-char "\n",
    it parses it as "\\n". This method decodes the string to handle escape
    sequences correctly.
    """
    if value is None:
        return None
    try:
        return codecs.decode(value, "unicode_escape")
    except Exception as e:
        raise click.BadParameter(f"Could not decode escape sequences: {e}") from e


@cli.command(
    help=(
        "Print out the available configuration settings that can be set "
        "through environment variables."
    )
)
def config():
    print_config()


@cli.group(help="General preprocessing tools and utilities.")
def preprocess():
    pass


@preprocess.command(
    help=(
        "Convert a dataset to have specific prompt and output token sizes.\n"
        "DATA: Path to the input dataset or dataset ID.\n"
        "OUTPUT_PATH: Path to save the converted dataset, including file suffix."
    )
)
@click.argument(
    "data",
    type=str,
    required=True,
)
@click.argument(
    "output_path",
    type=click.Path(file_okay=True, dir_okay=False, writable=True, resolve_path=True),
    required=True,
)
@click.option(
    "--processor",
    type=str,
    required=True,
    help=(
        "The processor or tokenizer to use to calculate token counts for statistics "
        "and synthetic data generation."
    ),
)
@click.option(
    "--processor-args",
    default=None,
    callback=parse_json,
    help=(
        "A JSON string containing any arguments to pass to the processor constructor "
        "as a dict with **kwargs."
    ),
)
@click.option(
    "--data-args",
    callback=parse_json,
    help=(
        "A JSON string containing any arguments to pass to the dataset creation "
        "as a dict with **kwargs."
    ),
)
@click.option(
    "--short-prompt-strategy",
    type=click.Choice([s.value for s in ShortPromptStrategy]),
    default=ShortPromptStrategy.IGNORE.value,
    show_default=True,
    help="Strategy to handle prompts shorter than the target length. ",
)
@click.option(
    "--pad-char",
    type=str,
    default="",
    callback=decode_escaped_str,
    help="The token to pad short prompts with when using the 'pad' strategy.",
)
@click.option(
    "--concat-delimiter",
    type=str,
    default="",
    help=(
        "The delimiter to use when concatenating prompts that are too short."
        " Used when strategy is 'concatenate'."
    ),
)
@click.option(
    "--prompt-tokens",
    type=str,
    default=None,
    help="Prompt tokens config (JSON, YAML file or key=value string)",
)
@click.option(
    "--output-tokens",
    type=str,
    default=None,
    help="Output tokens config (JSON, YAML file or key=value string)",
)
@click.option(
    "--push-to-hub",
    is_flag=True,
    help="Set this flag to push the converted dataset to the Hugging Face Hub.",
)
@click.option(
    "--hub-dataset-id",
    type=str,
    default=None,
    help="The Hugging Face Hub dataset ID to push to. "
    "Required if --push-to-hub is used.",
)
@click.option(
    "--random-seed",
    type=int,
    default=42,
    show_default=True,
    help="Random seed for prompt token sampling and output tokens sampling.",
)
def dataset(
    data,
    output_path,
    processor,
    processor_args,
    data_args,
    short_prompt_strategy,
    pad_char,
    concat_delimiter,
    prompt_tokens,
    output_tokens,
    push_to_hub,
    hub_dataset_id,
    random_seed,
):
    process_dataset(
        data=data,
        output_path=output_path,
        processor=processor,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        processor_args=processor_args,
        data_args=data_args,
        short_prompt_strategy=short_prompt_strategy,
        pad_char=pad_char,
        concat_delimiter=concat_delimiter,
        push_to_hub=push_to_hub,
        hub_dataset_id=hub_dataset_id,
        random_seed=random_seed,
    )


if __name__ == "__main__":
    cli()
