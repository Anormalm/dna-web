"""Run the frozen collector with float32 model parallelism for larger checkpoints."""
import os
import sys
from pathlib import Path
from common import read


def install(research):
    sys.path.insert(0, str(Path(research) / 'src'))
    from distdna.providers import transformers_local as provider
    original = provider.LocalTransformersGenerator

    class ParallelGenerator(original):
        def _ensure_model(self, model_id):
            if len(os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')) < 2:
                return super()._ensure_model(model_id)
            if self._model_id == model_id:
                return
            if model_id not in self.revisions:
                raise KeyError(model_id)
            self._load_runtime()
            self._release_model()
            revision, source = self.revisions[model_id], self.sources[model_id]
            tokenizer = self._transformers.AutoTokenizer.from_pretrained(
                source, revision=revision, local_files_only=True, trust_remote_code=False)
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token = tokenizer.eos_token
            count = self._torch.cuda.device_count()
            model = self._transformers.AutoModelForCausalLM.from_pretrained(
                source, revision=revision, dtype=self._torch.float32,
                local_files_only=True, trust_remote_code=False, low_cpu_mem_usage=True,
                use_safetensors=self.use_safetensors_by_model[model_id],
                device_map='balanced', max_memory={i: '64GiB' for i in range(count)})
            if any(str(device) in ('cpu', 'disk') for device in model.hf_device_map.values()):
                raise RuntimeError('Model would require CPU/disk offload; reserve more GPUs')
            if model.get_input_embeddings().weight.device.index != 0:
                raise RuntimeError('Expected input embeddings on the first visible GPU')
            resolved = getattr(model.config, '_commit_hash', None)
            if resolved is not None and resolved != revision:
                raise RuntimeError('Checkpoint revision mismatch')
            model.eval()
            provider._clear_inherited_max_length(model)
            self._model, self._tokenizer = model, tokenizer
            self._model_id, self._resolved_commit = model_id, revision
            if self.status:
                self.status(f'ready {model_id}@{revision}; float32 across {count} GPUs')

    provider.LocalTransformersGenerator = ParallelGenerator


if __name__ == '__main__':
    run = Path(sys.argv[sys.argv.index('--run') + 1])
    install(read(run / 'run.json')['research'])
    import collect
    collect.main()
