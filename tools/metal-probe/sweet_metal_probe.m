/*
 * 0127 probe: a Metal plugin that loads in backboardd and then declines.
 *
 * It exists to answer one question on gauguin: does iOS 16.7.16's Metal read
 * MetalPluginName from our IOAcceleratorES, dlopen the bundle and call
 * -initWithAcceleratorPort:?  The class is made at load time as a subclass of
 * _MTLDevice (the header is private), logs every step to stderr (backboardd's
 * stderr reaches the console ring) and to /private/var/tmp/sweet-metal-probe.log,
 * and returns nil so CoreAnimation stays on the CPU renderer.
 */
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <stdio.h>

static void
probe_log(const char *msg)
{
	FILE *f;

	fprintf(stderr, "SWEET METAL: %s\n", msg);
	fflush(stderr);
	f = fopen("/private/var/tmp/sweet-metal-probe.log", "a");
	if (f != NULL) {
		fprintf(f, "SWEET METAL: %s\n", msg);
		fclose(f);
	}
}

static id
probe_init(id self, SEL _cmd, unsigned int port)
{
	(void)self;
	(void)_cmd;
	(void)port;
	probe_log("-initWithAcceleratorPort: called, declining (nil)");
	return nil;
}

__attribute__((constructor))
static void
probe_load(void)
{
	Class base = objc_getClass("_MTLDevice");
	Class cls;

	if (base == Nil) {
		base = objc_getClass("NSObject");
	}
	cls = objc_allocateClassPair(base, "SweetMetalDevice", 0);
	if (cls == Nil) {
		probe_log("objc_allocateClassPair failed");
		return;
	}
	class_addMethod(cls, sel_registerName("initWithAcceleratorPort:"),
	    (IMP)probe_init, "@@:I");
	objc_registerClassPair(cls);
	probe_log("bundle loaded, SweetMetalDevice registered (_MTLDevice subclass)");
}
