from dify_plugin import DifyPluginEnv, Plugin


class MyClawPlugin(Plugin):
    def _init(self):
        pass


if __name__ == "__main__":
    MyClawPlugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=3600)).run()
