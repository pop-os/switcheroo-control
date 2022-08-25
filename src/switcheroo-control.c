/*
 * Copyright (c) 2016 Bastien Nocera <hadess@hadess.net>
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License version 3 as published by
 * the Free Software Foundation, or (at your option) any later version.
 *
 */

#define _GNU_SOURCE

#include <locale.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <gio/gio.h>
#include <gudev/gudev.h>

#include "info-cleanup.h"
#include "switcheroo-control-resources.h"

#define CONTROL_PROXY_DBUS_NAME          "net.hadess.SwitcherooControl"
#define CONTROL_PROXY_DBUS_PATH          "/net/hadess/SwitcherooControl"
#define CONTROL_PROXY_IFACE_NAME         CONTROL_PROXY_DBUS_NAME

typedef struct {
	GUdevDevice *dev;
	char *name;
	GPtrArray *env;
	gboolean is_default;
} CardData;

typedef struct {
	GMainLoop *loop;
	GDBusNodeInfo *introspection_data;
	GDBusConnection *connection;
	guint name_id;
	gboolean init_done;

	/* Detection */
	GUdevClient *client;
	gboolean add_fake_cards;
	guint num_gpus;
	GPtrArray *cards; /* array of CardData */
} ControlData;

static void
free_card_data (CardData *data)
{
	if (data == NULL)
		return;

	g_object_unref (data->dev);
	g_free (data->name);
	g_ptr_array_free (data->env, TRUE);
}

static void
free_control_data (ControlData *data)
{
	if (data == NULL)
		return;

	if (data->name_id != 0) {
		g_bus_unown_name (data->name_id);
		data->name_id = 0;
	}

	g_clear_object (&data->client);
	g_clear_pointer (&data->introspection_data, g_dbus_node_info_unref);
	g_clear_object (&data->connection);
	g_clear_pointer (&data->loop, g_main_loop_unref);
	g_free (data);
}

static GVariant *
build_gpus_variant (ControlData *data)
{
	GVariantBuilder builder;
	guint i;

	g_variant_builder_init (&builder, G_VARIANT_TYPE ("aa{sv}"));

	for (i = 0; i < data->cards->len; i++) {
		CardData *card = data->cards->pdata[i];
		GVariantBuilder asv_builder;

		g_variant_builder_init (&asv_builder, G_VARIANT_TYPE ("a{sv}"));
		g_variant_builder_add (&asv_builder, "{sv}", "Name", g_variant_new_string (card->name));
		g_variant_builder_add (&asv_builder, "{sv}", "Environment",
				       g_variant_new_strv ((const gchar * const *) card->env->pdata, card->env->len));
		g_variant_builder_add (&asv_builder, "{sv}", "Default",
				       g_variant_new_boolean (card->is_default));

		g_variant_builder_add (&builder, "a{sv}", &asv_builder);
	}

	return g_variant_builder_end (&builder);
}

static void
send_dbus_event (ControlData *data)
{
	GVariantBuilder props_builder;
	GVariant *props_changed = NULL;

	if (data->connection == NULL) {
		g_debug ("Not sending D-Bus event, D-Bus not ready");
		return;
	}

	g_variant_builder_init (&props_builder, G_VARIANT_TYPE ("a{sv}"));

	g_variant_builder_add (&props_builder, "{sv}", "HasDualGpu",
			       g_variant_new_boolean (data->num_gpus >= 2));
	g_variant_builder_add (&props_builder, "{sv}", "NumGPUs",
			       g_variant_new_uint32 (data->num_gpus));
	g_variant_builder_add (&props_builder, "{sv}", "GPUs",
			       build_gpus_variant (data));

	props_changed = g_variant_new ("(s@a{sv}@as)", CONTROL_PROXY_IFACE_NAME,
				       g_variant_builder_end (&props_builder),
				       g_variant_new_strv (NULL, 0));

	g_dbus_connection_emit_signal (data->connection,
				       NULL,
				       CONTROL_PROXY_DBUS_PATH,
				       "org.freedesktop.DBus.Properties",
				       "PropertiesChanged",
				       props_changed, NULL);
}

static GVariant *
handle_get_property (GDBusConnection *connection,
		     const gchar     *sender,
		     const gchar     *object_path,
		     const gchar     *interface_name,
		     const gchar     *property_name,
		     GError         **error,
		     gpointer         user_data)
{
	ControlData *data = user_data;

	g_assert (data->connection);

	if (g_strcmp0 (property_name, "HasDualGpu") == 0)
		return g_variant_new_boolean (data->num_gpus >= 2);
	if (g_strcmp0 (property_name, "NumGPUs") == 0)
		return g_variant_new_uint32 (data->num_gpus);
	if (g_strcmp0 (property_name, "GPUs") == 0)
		return build_gpus_variant (data);

	return NULL;
}

static const GDBusInterfaceVTable interface_vtable =
{
	NULL,
	handle_get_property,
	NULL
};

static void
name_lost_handler (GDBusConnection *connection,
		   const gchar     *name,
		   gpointer         user_data)
{
	g_debug ("switcheroo-control is already running, or it cannot own its D-Bus name. Verify installation.");
	exit (0);
}

static void
bus_acquired_handler (GDBusConnection *connection,
		      const gchar     *name,
		      gpointer         user_data)
{
	ControlData *data = user_data;

	g_dbus_connection_register_object (connection,
					   CONTROL_PROXY_DBUS_PATH,
					   data->introspection_data->interfaces[0],
					   &interface_vtable,
					   data,
					   NULL,
					   NULL);

	data->connection = g_object_ref (connection);
}

static void
name_acquired_handler (GDBusConnection *connection,
		       const gchar     *name,
		       gpointer         user_data)
{
	ControlData *data = user_data;

	if (data->init_done)
		send_dbus_event (data);
}

static gboolean
setup_dbus (ControlData *data,
	    gboolean     replace)
{
	GBytes *bytes;
	GBusNameOwnerFlags flags;

	bytes = g_resources_lookup_data ("/net/hadess/SwitcherooControl/net.hadess.SwitcherooControl.xml",
					 G_RESOURCE_LOOKUP_FLAGS_NONE,
					 NULL);
	data->introspection_data = g_dbus_node_info_new_for_xml (g_bytes_get_data (bytes, NULL), NULL);
	g_bytes_unref (bytes);
	g_assert (data->introspection_data != NULL);

	flags = G_BUS_NAME_OWNER_FLAGS_ALLOW_REPLACEMENT;
	if (replace)
		flags |= G_BUS_NAME_OWNER_FLAGS_REPLACE;

	data->name_id = g_bus_own_name (G_BUS_TYPE_SYSTEM,
					CONTROL_PROXY_DBUS_NAME,
					flags,
					bus_acquired_handler,
					name_acquired_handler,
					name_lost_handler,
					data,
					NULL);

	return TRUE;
}

static GPtrArray *
get_card_env (GUdevClient *client,
	      GUdevDevice *dev)
{
	GPtrArray *array;
	g_autoptr(GUdevDevice) parent = NULL;

	array = g_ptr_array_new_full (0, g_free);

	parent = g_udev_device_get_parent (dev);
	if (g_strcmp0 (g_udev_device_get_driver (parent), "nvidia") == 0) {
		g_ptr_array_add (array, g_strdup ("__GLX_VENDOR_LIBRARY_NAME"));
		g_ptr_array_add (array, g_strdup ("nvidia"));

		/* XXX: __NV_PRIME_RENDER_OFFLOAD_PROVIDER would be needed for
		 * multi-NVidia setups, see:
		 * https://download.nvidia.com/XFree86/Linux-x86_64/440.26/README/primerenderoffload.html */
		g_ptr_array_add (array, g_strdup ("__NV_PRIME_RENDER_OFFLOAD"));
		g_ptr_array_add (array, g_strdup ("1"));

		/* Make sure Vulkan apps always select Nvidia GPUs */
		g_ptr_array_add (array, g_strdup ("__VK_LAYER_NV_optimus"));
		g_ptr_array_add (array, g_strdup ("NVIDIA_only"));
	} else {
		char *id;

		/* See the Mesa loader code:
		 * https://gitlab.freedesktop.org/mesa/mesa/blob/master/src/loader/loader.c#L322 */
		id = g_strdup (g_udev_device_get_property (dev, "ID_PATH_TAG"));
		if (id != NULL) {
			g_ptr_array_add (array, g_strdup ("DRI_PRIME"));
			g_ptr_array_add (array, id);
		}
	}

	if (array->len == 0) {
		g_ptr_array_free (array, TRUE);
		return NULL;
	}

	return array;
}

static char *
get_card_name (GUdevDevice *d)
{
	const char *vendor, *product;
	g_autoptr(GUdevDevice) parent = NULL;
	g_autofree char *renderer = NULL;

	parent = g_udev_device_get_parent (d);
	vendor = g_udev_device_get_property (parent, "SWITCHEROO_CONTROL_VENDOR_NAME");
	if (!vendor || *vendor == '\0')
		vendor = g_udev_device_get_property (parent, "ID_VENDOR_FROM_DATABASE");
	product = g_udev_device_get_property (parent, "SWITCHEROO_CONTROL_PRODUCT_NAME");
	if (!product || *product == '\0')
		product = g_udev_device_get_property (parent, "ID_MODEL_FROM_DATABASE");

	if (!vendor && !product)
		goto bail;

	if (!vendor)
		return g_strdup (product);
	if (!product)
		return g_strdup (vendor);
	renderer = g_strdup_printf ("%s %s", vendor, product);
	return info_cleanup (renderer);

bail:
	return g_strdup ("Unknown Graphics Controller");
}

static gboolean
get_card_is_default (GUdevDevice *d)
{
	g_autoptr(GUdevDevice) parent = NULL;

	parent = g_udev_device_get_parent (d);
	return g_udev_device_get_sysfs_attr_as_boolean (parent, "boot_vga");
}

static CardData *
get_card_data (GUdevClient *client,
	       GUdevDevice *d)
{
	CardData *data;
	GPtrArray *env;

	env = get_card_env (client, d);
	if (!env)
		return NULL;

	data = g_new0 (CardData, 1);
	data->dev = g_object_ref (d);
	data->name = get_card_name (d);
	data->env = env;
	data->is_default = get_card_is_default (d);

	return data;
}

static void
add_fake_intel_card (GPtrArray *cards)
{
	CardData *card;
	const char *env[] = {
		"INTEL_AGP_OFFLOADING", "1",
		"INTEL_PCI_MODE", "false",
		NULL
	};
	guint i;

	card = g_new0 (CardData, 1);
	card->name = "Intel i740 “Auburn”";
	card->env = g_ptr_array_new ();
	for (i = 0; env[i] != NULL; i++)
		g_ptr_array_add (card->env, g_strdup (env[i]));

	g_ptr_array_add (cards, card);
}

static void
add_fake_trident_card (GPtrArray *cards)
{
	CardData *card;
	const char *env[] = {
		"TRIDENT_OFFLOADING", "1",
		NULL
	};
	guint i;

	card = g_new0 (CardData, 1);
	card->name = "Trident Vesa Local Bus 512KB";
	card->env = g_ptr_array_new ();
	for (i = 0; env[i] != NULL; i++)
		g_ptr_array_add (card->env, g_strdup (env[i]));

	g_ptr_array_add (cards, card);
}

static GPtrArray *
get_drm_cards (ControlData *data)
{
	GList *devices, *l;
	GPtrArray *cards;

	cards = g_ptr_array_new_with_free_func ((GDestroyNotify) free_card_data);

	if (data->add_fake_cards)
		add_fake_intel_card (cards);

	devices = g_udev_client_query_by_subsystem (data->client, "drm");
	for (l = devices; l != NULL; l = l->next) {
		GUdevDevice *d = l->data;
		const char *path;

		path = g_udev_device_get_device_file (d);
		if (path != NULL &&
		    g_str_has_prefix (path, "/dev/dri/render")) {
			CardData *card;
			card = get_card_data (data->client, d);
			if (card)
				g_ptr_array_add (cards, card);
		}
		g_object_unref (d);
	}
	g_list_free (devices);

	if (data->add_fake_cards)
		add_fake_trident_card (cards);

	/* Make sure the only card is the default */
	if (cards->len == 1) {
		CardData *card = cards->pdata[0];
		card->is_default = TRUE;
	}

	return cards;
}

static void
uevent_cb (GUdevClient *client,
	   gchar       *action,
	   GUdevDevice *device,
	   gpointer     user_data)
{
	ControlData *data = user_data;
	GPtrArray *cards;
	guint num_gpus;

	cards = get_drm_cards (data);
	num_gpus = cards->len;
	if (num_gpus != data->num_gpus) {
		g_debug ("GPUs added or removed (old: %d new: %d)",
			 data->num_gpus, num_gpus);
		g_ptr_array_free (data->cards, TRUE);
		data->cards = cards;
		data->num_gpus = cards->len;
		send_dbus_event (data);
	} else {
		g_ptr_array_free (cards, TRUE);
	}
}

static void
get_num_gpus (ControlData *data)
{
	const gchar * const subsystem[] = { "drm", NULL };

	data->client = g_udev_client_new (subsystem);
	data->cards = get_drm_cards (data);
	data->num_gpus = data->cards->len;

	g_signal_connect (G_OBJECT (data->client), "uevent",
			  G_CALLBACK (uevent_cb), data);
}

int main (int argc, char **argv)
{
	ControlData *data;
	g_autoptr(GOptionContext) option_context = NULL;
	g_autoptr(GError) error = NULL;
	gboolean verbose = FALSE;
	gboolean add_fake_cards = FALSE;
	gboolean replace = FALSE;
	gboolean ret;
	const GOptionEntry options[] = {
		{ "verbose", 'v', 0, G_OPTION_ARG_NONE, &verbose, "Show extra debugging information", NULL },
		{ "fake", 'f', 0, G_OPTION_ARG_NONE, &add_fake_cards, "Add fake GPUs to the output", NULL },
		{ "replace", 'r', 0, G_OPTION_ARG_NONE, &replace, "Replace the running instance of switcheroo-control", NULL },
		{ NULL}
	};

	setlocale (LC_ALL, "");
	option_context = g_option_context_new ("");
	g_option_context_add_main_entries (option_context, options, NULL);

	ret = g_option_context_parse (option_context, &argc, &argv, &error);
	if (!ret) {
		g_print ("Failed to parse arguments: %s\n", error->message);
		return EXIT_FAILURE;
	}

	if (verbose)
		g_setenv ("G_MESSAGES_DEBUG", "all", TRUE);

	data = g_new0 (ControlData, 1);
	data->add_fake_cards = add_fake_cards;

	get_num_gpus (data);
	setup_dbus (data, replace);
	data->init_done = TRUE;
	if (data->connection)
		send_dbus_event (data);

	data->loop = g_main_loop_new (NULL, TRUE);
	g_main_loop_run (data->loop);

	free_control_data (data);

	return 0;
}
