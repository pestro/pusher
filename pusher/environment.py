import os
import sys
import logging

logger = logging.getLogger(__name__)

from .components import root_objects
from .archive import Archive

class PusherEnvironment(object):
  def __init__(self, root_path, config, **kw):
    """
    Initialize the environment with each specific amount of servers and modules.
    """
    self.root = root_path
    self.config = config

    self.archive = Archive(self, config.get("archive", ".archive"))
    self.archive.create()

    for klass in root_objects:
      setattr(self, klass.__group__, kw.pop(klass.__group__, {}))

  def shutdown(self):
    for klass in root_objects:
      for k,v in getattr(self, klass.__group__).items():
        v.close()

  def setup(self):
    for deploy in self.deploys.values():
      deploy.setup(self)

  def list_commands(self):
    ff = filter(lambda n: n.endswith("_run"), dir(self))
    return [(c, self.get_help_for(c)) for c in [(n[:-4].lower()) for n in ff]]

  def get_command(self, command):
    command     = command.strip().upper()
    run_c       = "{}_run".format(command)
    validate_c  = "{}_validate".format(command)

    if not hasattr(self, run_c):
      raise RuntimeError, "no such command: " + command

    if not hasattr(self, validate_c):
      validator = lambda a: a
    else:
      validator = getattr(self, validate_c)

    run = getattr(self, run_c)
    return validator, run

  def UPDATE_validate(self, args):
    if len(args) != 2:
      raise RuntimeError, "Number of arguments must be exactly 2"
    if not self.contains(args[0]):
      raise RuntimeError, "Environment does not contain stage: " + args[0]
    return args, {}

  def UPDATE_run(self, stage, version):
    """
    @usage update <stage> <version>
    @short Download and archive all the specified <stage>+<version>
    """
    deploy = self.deploys.get(stage, None)

    if not deploy:
      logger.error("No such stage: " + stage)
      return False

    for module in deploy.modules:
      if not self.archive.contains(module, stage, version):
        tar = self.archive.new_tar(module, stage, version)

        for url in module.geturls(version):
          print "Downloading into", "{}-{}".format(module.name, version), "from", url.geturl()

          try:
            tar.download(url)
          except Exception, e:
            import sys
            exc = sys.exc_info()
            logger.error("Failed to download {}: {}".format(url.geturl(), str(e)))
            tar.unfinished()
            raise exc[0], None, exc[2]

        tar.close()

    return True

  def CHECK_validate(self, args):
    if len(args) != 1:
      raise RuntimeError, "Number of arguments must be exactly 1"
    if not self.contains(args[0]):
      raise RuntimeError, "Environment does not contain stage: " + args[0]
    return args, {}

  def CHECK_run(self, stage):
    """
    @usage check <stage>
    @short Execute all configured checks for a specific <stage>.
    @desc
    See the pusher.yaml configuration for details.
    """
    deploy = self.deploys.get(stage, None)

    if not deploy:
      logger.error("No such stage: " + stage)
      return False

    i = 0

    ok = True

    for server in deploy.servers:
      for check in deploy.checks:
        line = "Check #{:03} {}:".format(i, check.name)
        print line, "Running", check, "on", server

        try:
          exitcode, stdout, stderr = check.run(self, server)
        except RuntimeError, e:
          logger.error("Check failed: {}".format(str(e)))
          return False

        def print_out(name, s):
          for l in s.split("\n"):
            if l.strip() == "": l = "(empty)"
            print line, name, l

        if stdout: print_out("stdout:", stdout)
        if stderr: print_out("stderr:", stderr)

        print line, "Exited with", exitcode
        
        if exitcode != 0:
          logger.info("Check returned non-zero exit status")
          ok = False

        i += 1

    return ok

  def DEPLOY_validate(self, args):
    if len(args) != 2:
      raise RuntimeError, "Number of arguments must be exactly 2"
    if not self.contains(args[0]):
      raise RuntimeError, "Environment does not contain stage: " + args[0]
    return args, {}

  def DEPLOY_run(self, stage, version):
    """
    @usage deploy <stage> <version>
    @short Deploy artifacts for <stage>+<version>
    @desc
    See the pusher.yaml configuration for details.
    """
    deploy = self.deploys.get(stage, None)

    if not deploy:
      logger.error("No such stage: " + stage)
      return False

    all_ok = True
    for module in deploy.modules:
      if not self.archive.contains(module, stage, version):
        print "not in archive (run update)", self.archive.module_path(module, stage, version)
        all_ok = False

    if not all_ok:
      return False

    for server in deploy.servers:
      for module in deploy.modules:
        source = self.archive.open(module, stage, version)
        print "Deploying {} (version {}) to {}".format(deploy, version, server)

        try:
          module.deploy(server, source, deploy, version)
        finally:
          source.close()

    return True

  def HELP_validate(self, args):
    if len(args) != 1:
      raise RuntimeError, "Number of arguments must be exactly 1"
    return args, {}

  def HELP_run(self, command):
    """
    @usage help <command>
    @short Print docs for <command>
    """

    try:
      short, usage, docs = self.get_help_for(command)
    except:
      print >> sys.stderr, "No such command:", command
      return False

    print >> sys.stdout, "Usage:", usage
    print >> sys.stdout, "Short:", short
    print >> sys.stdout, "\n".join(docs)
    return True

  def parse_help_for(self, func_doc):
    short = None
    usage = None
    desc = False
    docs = list()

    for line in func_doc.split("\n"):
      line = line.strip()
      if desc:
        docs.append(line)
        continue
      if line.startswith("@usage"): usage = line[7:]
      if line.startswith("@short"): short = line[7:]
      if line.startswith("@desc"): desc = True

    return short, usage, docs

  def get_help_for(self, command):
    _, run = self.get_command(command)
    return self.parse_help_for(run.func_doc)

  def contains(self, stage):
    return stage in self.deploys

def validate_config(c):
  def check_type(k, c, vtype):
    if k not in c:
      raise RuntimeError, "{}: missing key".format(k)
    rtype = type(c[k])
    if not isinstance(c[k], vtype):
      raise RuntimeError, "{}: should be '{}' but is '{}'".format(k, vtype.__name__, rtype.__name__)

  def valid_keys(c, vtype, *keys):
    for k in keys:
      check_type(k, c, vtype)

  def valid_component(c, klass):
    if type(c) != dict:
      raise RuntimeError, "type is not 'dict'"

    for k, vtype in klass.__keys__.items():
      check_type(k, c, vtype)

  def each_key(conf, name, func):
    for i, (k, v) in enumerate(conf[name].items()):
      if v is None:
        raise RuntimeError, ("{}#{} \"{}\": value is null".format(name, i, k))

      try:
        func(v)
      except RuntimeError, e:
        raise RuntimeError, ("{}#{} \"{}\": {}".format(name, i, k, str(e)))

  valid_keys(c, dict, *[k.__group__ for k in root_objects])

  for klass in root_objects:
    each_key(c, klass.__group__, lambda c: valid_component(c, klass))

def create_components(environ, config, klass):
  comps = dict()
  for k,v in environ[klass.__group__].items():
    pconf = dict(config)
    pconf["name"] = k
    pconf.update(v)
    comp = klass(pconf)
    for attr in klass.__keys__.keys():
      val = v.get(attr, None)
      comp.set_attr(attr, val)
    comps[k] = comp
  return comps

def create_env(root, environ, opts):
  try:
    validate_config(environ)
  except RuntimeError, e:
    raise RuntimeError, "Invalid schema: "+ str(e)

  config = dict()

  if "config" in environ:
    config.update(environ["config"])

  config.update(os.environ)

  config["root"] = root
  config["cwd"] = os.getcwd()

  config.update(opts)
  cache = dict(config)

  for k in config:
    config[k] = config[k].format(**cache)

  objects = dict()
  
  for klass in root_objects:
    objects[klass.__group__] = create_components(environ, config, klass)

  env = PusherEnvironment(root, config, **objects)

  try:
    env.setup()
  except RuntimeError, e:
    raise RuntimeError, "Invalid environment: {}".format(str(e))

  return env
