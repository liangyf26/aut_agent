class HeuristicLlmProvider {
  constructor(config = {}) {
    this.name = config.name || 'local-heuristic';
    this.mode = 'offline';
  }

  async generateJson(taskName, input, generator) {
    const result = generator(input);
    return {
      provider: this.name,
      taskName,
      generatedAt: new Date().toISOString(),
      result
    };
  }
}

class OpenAiCompatibleProvider {
  constructor(config = {}) {
    this.name = config.name || 'openai-compatible';
    this.baseUrl = config.baseUrl;
    this.model = config.model;
  }

  async generateJson() {
    throw new Error('OpenAI-compatible provider is configured but not wired in this MVP.');
  }
}

function createLlmProvider(config = {}) {
  if (config.type === 'openai-compatible') {
    return new OpenAiCompatibleProvider(config);
  }

  return new HeuristicLlmProvider(config);
}

module.exports = {
  HeuristicLlmProvider,
  OpenAiCompatibleProvider,
  createLlmProvider
};
