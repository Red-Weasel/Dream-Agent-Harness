"""Share observed review/advisor usage with the owning run, never global context."""


class AttributedMeter:
    def __init__(self, meter, label):
        self.meter, self.label = meter, label

    def check(self):
        self.meter.check()

    def before_tool(self, name):
        self.meter.before_tool(name)

    def usage(self, usage, phase='lead'):
        keys = ('prompt_tokens', 'input_tokens', 'completion_tokens', 'output_tokens',
                'cache_read_input_tokens', 'cache_creation_input_tokens')
        if not isinstance(usage, dict) or not any(key in usage for key in keys):
            return
        if any(key in usage and (type(usage[key]) is not int or usage[key] < 0) for key in keys):
            return
        self.meter.usage(usage, phase=f'{self.label}:{phase}')


def attributed_meter(provider, *, scope, meter=None):
    if meter is None:
        from ..tools.context import bound_runtime_meter
        meter = bound_runtime_meter()
    return AttributedMeter(meter, f'{scope}:{provider.key}') if meter is not None else None
