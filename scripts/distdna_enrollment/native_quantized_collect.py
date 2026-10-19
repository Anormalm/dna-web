"""Preserve a catalog checkpoint's native bitsandbytes quantization on A100 GPUs.

This adapter changes model loading only. Prompt collection, stopping, seeds,
response encoding and reference construction use the standard collector.
"""
import importlib.metadata
import sys
from pathlib import Path
from common import read


def verify_gpu_placement(model):
    # Transformers may omit hf_device_map for a single-device quantized model.
    # Inspect actual parameter devices as well as any reported placement map.
    placements = getattr(model, 'hf_device_map', {})
    if any(str(d) in ('cpu', 'disk', 'meta') for d in placements.values()):
        raise RuntimeError('Quantized model requires more GPU memory')
    parameters = list(model.parameters())
    if not parameters or any(p.device.type != 'cuda' for p in parameters):
        raise RuntimeError('Quantized parameters are not fully on GPU')
    if model.get_input_embeddings().weight.device.index != 0:
        raise RuntimeError('Expected input embeddings on first visible GPU')


def install(spec):
    runtime = spec['nativeRuntime']
    if importlib.metadata.version('bitsandbytes') != runtime['version']:
        raise ValueError('Native quantization dependency version changed')
    sys.path.insert(0, str(Path(spec['research'])/'src'))
    from distdna.providers import transformers_local as provider
    original = provider.LocalTransformersGenerator

    class NativeGenerator(original):
        def _ensure_model(self, model_id):
            if self._model_id == model_id:
                return
            self._load_runtime()
            self._release_model()
            source, revision = self.sources[model_id], self.revisions[model_id]
            source_config = read(Path(source)/'config.json')
            if source_config.get('quantization_config', {}).get('quant_method') != 'bitsandbytes':
                raise ValueError('This runtime accepts native bitsandbytes checkpoints only')
            tokenizer = self._transformers.AutoTokenizer.from_pretrained(source,
                revision=revision, local_files_only=True, trust_remote_code=False)
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token = tokenizer.eos_token
            count = self._torch.cuda.device_count()
            model = self._transformers.AutoModelForCausalLM.from_pretrained(source,
                revision=revision, dtype=getattr(self._torch, self.dtype_name),
                local_files_only=True, trust_remote_code=False, low_cpu_mem_usage=True,
                use_safetensors=self.use_safetensors_by_model[model_id],
                device_map={'': 0} if count == 1 else 'balanced',
                max_memory={i: '64GiB' for i in range(count)})
            if not getattr(model, 'is_quantized', False):
                raise RuntimeError('Native quantized weights were not loaded as quantized')
            verify_gpu_placement(model)
            resolved = getattr(model.config, '_commit_hash', None)
            if resolved is not None and resolved != revision:
                raise RuntimeError('Checkpoint revision mismatch')
            model.eval()
            provider._clear_inherited_max_length(model)
            self._model, self._tokenizer = model, tokenizer
            self._model_id, self._resolved_commit = model_id, revision
            if self.status:
                self.status(f'ready {model_id}@{revision}; native bitsandbytes, {self.dtype_name}, {count} GPUs')

    provider.LocalTransformersGenerator = NativeGenerator


if __name__ == '__main__':
    folder = Path(sys.argv[sys.argv.index('--run') + 1])
    install(read(folder/'run.json'))
    import collect
    collect.main()
