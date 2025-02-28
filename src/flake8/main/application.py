"""Module containing the application logic for Flake8."""
import argparse
import configparser
import json
import logging
import sys
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Set
from typing import Tuple
from typing import Type
from typing import TYPE_CHECKING

import flake8
from flake8 import checker
from flake8 import defaults
from flake8 import exceptions
from flake8 import style_guide
from flake8 import utils
from flake8.main import debug
from flake8.main import options
from flake8.options import aggregator
from flake8.options import config
from flake8.options import manager
from flake8.plugins import manager as plugin_manager

if TYPE_CHECKING:
    from flake8.formatting.base import BaseFormatter


LOG = logging.getLogger(__name__)


class Application:
    """Abstract our application into a class."""

    def __init__(self) -> None:
        """Initialize our application.

        :param str program:
            The name of the program/application that we're executing.
        :param str version:
            The version of the program/application we're executing.
        """
        #: The timestamp when the Application instance was instantiated.
        self.start_time = time.time()
        #: The timestamp when the Application finished reported errors.
        self.end_time: Optional[float] = None
        #: The prelimary argument parser for handling options required for
        #: obtaining and parsing the configuration file.
        self.prelim_arg_parser = options.stage1_arg_parser()
        #: The instance of :class:`flake8.options.manager.OptionManager` used
        #: to parse and handle the options and arguments passed by the user
        self.option_manager: Optional[manager.OptionManager] = None

        #: The instance of :class:`flake8.plugins.manager.Checkers`
        self.check_plugins: Optional[plugin_manager.Checkers] = None
        #: The instance of :class:`flake8.plugins.manager.ReportFormatters`
        self.formatting_plugins: Optional[
            plugin_manager.ReportFormatters
        ] = None
        #: The user-selected formatter from :attr:`formatting_plugins`
        self.formatter: Optional[BaseFormatter] = None
        #: The :class:`flake8.style_guide.StyleGuideManager` built from the
        #: user's options
        self.guide: Optional[style_guide.StyleGuideManager] = None
        #: The :class:`flake8.checker.Manager` that will handle running all of
        #: the checks selected by the user.
        self.file_checker_manager: Optional[checker.Manager] = None

        #: The user-supplied options parsed into an instance of
        #: :class:`argparse.Namespace`
        self.options: Optional[argparse.Namespace] = None
        #: The number of errors, warnings, and other messages after running
        #: flake8 and taking into account ignored errors and lines.
        self.result_count = 0
        #: The total number of errors before accounting for ignored errors and
        #: lines.
        self.total_result_count = 0
        #: Whether or not something catastrophic happened and we should exit
        #: with a non-zero status code
        self.catastrophic_failure = False

        #: The parsed diff information
        self.parsed_diff: Dict[str, Set[int]] = {}

    def parse_preliminary_options(
        self, argv: Sequence[str]
    ) -> Tuple[argparse.Namespace, List[str]]:
        """Get preliminary options from the CLI, pre-plugin-loading.

        We need to know the values of a few standard options so that we can
        locate configuration files and configure logging.

        Since plugins aren't loaded yet, there may be some as-yet-unknown
        options; we ignore those for now, they'll be parsed later when we do
        real option parsing.

        :param list argv:
            Command-line arguments passed in directly.
        :returns:
            Populated namespace and list of remaining argument strings.
        :rtype:
            (argparse.Namespace, list)
        """
        args, rest = self.prelim_arg_parser.parse_known_args(argv)
        # XXX (ericvw): Special case "forwarding" the output file option so
        # that it can be reparsed again for the BaseFormatter.filename.
        if args.output_file:
            rest.extend(("--output-file", args.output_file))
        return args, rest

    def exit_code(self) -> int:
        """Return the program exit code."""
        if self.catastrophic_failure:
            return 1
        assert self.options is not None
        if self.options.exit_zero:
            return 0
        else:
            return int(self.result_count > 0)

    def find_plugins(
        self,
        cfg: configparser.RawConfigParser,
        cfg_dir: str,
    ) -> None:
        """Find and load the plugins for this application.

        Set the :attr:`check_plugins` and :attr:`formatting_plugins` attributes
        based on the discovered plugins found.
        """
        # TODO: move to src/flake8/plugins/finder.py
        extension_local = utils.parse_comma_separated_list(
            cfg.get("flake8:local-plugins", "extension", fallback="").strip(),
            regexp=utils.LOCAL_PLUGIN_LIST_RE,
        )
        report_local = utils.parse_comma_separated_list(
            cfg.get("flake8:local-plugins", "report", fallback="").strip(),
            regexp=utils.LOCAL_PLUGIN_LIST_RE,
        )

        paths_s = cfg.get("flake8:local-plugins", "paths", fallback="").strip()
        local_paths = utils.parse_comma_separated_list(paths_s)
        local_paths = utils.normalize_paths(local_paths, cfg_dir)

        sys.path.extend(local_paths)

        self.check_plugins = plugin_manager.Checkers(extension_local)

        self.formatting_plugins = plugin_manager.ReportFormatters(report_local)

        self.check_plugins.load_plugins()
        self.formatting_plugins.load_plugins()

    def register_plugin_options(self) -> None:
        """Register options provided by plugins to our option manager."""
        assert self.check_plugins is not None
        assert self.formatting_plugins is not None

        versions = sorted(set(self.check_plugins.manager.versions()))
        self.option_manager = manager.OptionManager(
            version=flake8.__version__,
            plugin_versions=", ".join(
                f"{name}: {version}" for name, version in versions
            ),
            parents=[self.prelim_arg_parser],
        )
        options.register_default_options(self.option_manager)

        self.check_plugins.register_options(self.option_manager)
        self.formatting_plugins.register_options(self.option_manager)

    def parse_configuration_and_cli(
        self,
        cfg: configparser.RawConfigParser,
        cfg_dir: str,
        argv: List[str],
    ) -> None:
        """Parse configuration files and the CLI options."""
        assert self.option_manager is not None
        self.options = aggregator.aggregate_options(
            self.option_manager,
            cfg,
            cfg_dir,
            argv,
        )

        if self.options.bug_report:
            assert self.check_plugins is not None
            info = debug.information(flake8.__version__, self.check_plugins)
            print(json.dumps(info, indent=2, sort_keys=True))
            raise SystemExit(0)

        if self.options.diff:
            LOG.warning(
                "the --diff option is deprecated and will be removed in a "
                "future version."
            )
            self.parsed_diff = utils.parse_unified_diff()

        assert self.check_plugins is not None
        self.check_plugins.provide_options(
            self.option_manager, self.options, self.options.filenames
        )
        assert self.formatting_plugins is not None
        self.formatting_plugins.provide_options(
            self.option_manager, self.options, self.options.filenames
        )

    def formatter_for(self, formatter_plugin_name):
        """Retrieve the formatter class by plugin name."""
        assert self.formatting_plugins is not None
        default_formatter = self.formatting_plugins["default"]
        formatter_plugin = self.formatting_plugins.get(formatter_plugin_name)
        if formatter_plugin is None:
            LOG.warning(
                '"%s" is an unknown formatter. Falling back to default.',
                formatter_plugin_name,
            )
            formatter_plugin = default_formatter

        return formatter_plugin.execute

    def make_formatter(
        self, formatter_class: Optional[Type["BaseFormatter"]] = None
    ) -> None:
        """Initialize a formatter based on the parsed options."""
        assert self.options is not None
        format_plugin = self.options.format
        if 1 <= self.options.quiet < 2:
            format_plugin = "quiet-filename"
        elif 2 <= self.options.quiet:
            format_plugin = "quiet-nothing"

        if formatter_class is None:
            formatter_class = self.formatter_for(format_plugin)

        self.formatter = formatter_class(self.options)

    def make_guide(self) -> None:
        """Initialize our StyleGuide."""
        assert self.formatter is not None
        assert self.options is not None
        self.guide = style_guide.StyleGuideManager(
            self.options, self.formatter
        )

        if self.options.diff:
            self.guide.add_diff_ranges(self.parsed_diff)

    def make_file_checker_manager(self) -> None:
        """Initialize our FileChecker Manager."""
        self.file_checker_manager = checker.Manager(
            style_guide=self.guide,
            checker_plugins=self.check_plugins,
        )

    def run_checks(self) -> None:
        """Run the actual checks with the FileChecker Manager.

        This method encapsulates the logic to make a
        :class:`~flake8.checker.Manger` instance run the checks it is
        managing.
        """
        assert self.options is not None
        assert self.file_checker_manager is not None
        if self.options.diff:
            files: Optional[List[str]] = sorted(self.parsed_diff)
            if not files:
                return
        else:
            files = None

        self.file_checker_manager.start(files)
        try:
            self.file_checker_manager.run()
        except exceptions.PluginExecutionFailed as plugin_failed:
            print(str(plugin_failed))
            print("Run flake8 with greater verbosity to see more details")
            self.catastrophic_failure = True
        LOG.info("Finished running")
        self.file_checker_manager.stop()
        self.end_time = time.time()

    def report_benchmarks(self) -> None:
        """Aggregate, calculate, and report benchmarks for this run."""
        assert self.options is not None
        if not self.options.benchmark:
            return

        assert self.file_checker_manager is not None
        assert self.end_time is not None
        time_elapsed = self.end_time - self.start_time
        statistics = [("seconds elapsed", time_elapsed)]
        add_statistic = statistics.append
        for statistic in defaults.STATISTIC_NAMES + ("files",):
            value = self.file_checker_manager.statistics[statistic]
            total_description = f"total {statistic} processed"
            add_statistic((total_description, value))
            per_second_description = f"{statistic} processed per second"
            add_statistic((per_second_description, int(value / time_elapsed)))

        assert self.formatter is not None
        self.formatter.show_benchmarks(statistics)

    def report_errors(self) -> None:
        """Report all the errors found by flake8 3.0.

        This also updates the :attr:`result_count` attribute with the total
        number of errors, warnings, and other messages found.
        """
        LOG.info("Reporting errors")
        assert self.file_checker_manager is not None
        results = self.file_checker_manager.report()
        self.total_result_count, self.result_count = results
        LOG.info(
            "Found a total of %d violations and reported %d",
            self.total_result_count,
            self.result_count,
        )

    def report_statistics(self) -> None:
        """Aggregate and report statistics from this run."""
        assert self.options is not None
        if not self.options.statistics:
            return

        assert self.formatter is not None
        assert self.guide is not None
        self.formatter.show_statistics(self.guide.stats)

    def initialize(self, argv: Sequence[str]) -> None:
        """Initialize the application to be run.

        This finds the plugins, registers their options, and parses the
        command-line arguments.
        """
        # NOTE(sigmavirus24): When updating this, make sure you also update
        # our legacy API calls to these same methods.
        prelim_opts, remaining_args = self.parse_preliminary_options(argv)
        flake8.configure_logging(prelim_opts.verbose, prelim_opts.output_file)

        cfg, cfg_dir = config.load_config(
            config=prelim_opts.config,
            extra=prelim_opts.append_config,
            isolated=prelim_opts.isolated,
        )

        self.find_plugins(cfg, cfg_dir)
        self.register_plugin_options()
        self.parse_configuration_and_cli(cfg, cfg_dir, remaining_args)
        self.make_formatter()
        self.make_guide()
        self.make_file_checker_manager()

    def report(self) -> None:
        """Report errors, statistics, and benchmarks."""
        assert self.formatter is not None
        self.formatter.start()
        self.report_errors()
        self.report_statistics()
        self.report_benchmarks()
        self.formatter.stop()

    def _run(self, argv: Sequence[str]) -> None:
        self.initialize(argv)
        self.run_checks()
        self.report()

    def run(self, argv: Sequence[str]) -> None:
        """Run our application.

        This method will also handle KeyboardInterrupt exceptions for the
        entirety of the flake8 application. If it sees a KeyboardInterrupt it
        will forcibly clean up the :class:`~flake8.checker.Manager`.
        """
        try:
            self._run(argv)
        except KeyboardInterrupt as exc:
            print("... stopped")
            LOG.critical("Caught keyboard interrupt from user")
            LOG.exception(exc)
            self.catastrophic_failure = True
        except exceptions.ExecutionError as exc:
            print("There was a critical error during execution of Flake8:")
            print(exc)
            LOG.exception(exc)
            self.catastrophic_failure = True
        except exceptions.EarlyQuit:
            self.catastrophic_failure = True
            print("... stopped while processing files")
        else:
            assert self.options is not None
            if self.options.count:
                print(self.result_count)
