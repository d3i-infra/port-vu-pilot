import logging
import json
import io

import pandas as pd

import port.api.props as props
import port.helpers as helpers
import port.youtube as youtube
import port.validate as validate
import port.tiktok as tiktok

from port.api.commands import (CommandSystemDonate, CommandUIRender)

LOG_STREAM = io.StringIO()

logging.basicConfig(
    #stream=LOG_STREAM,
    level=logging.DEBUG,
    format="%(asctime)s --- %(name)s --- %(levelname)s --- %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

LOGGER = logging.getLogger("script")


def process(session_id):
    LOGGER.info("Starting the donation flow")
    yield donate_logs(f"{session_id}-tracking")

    platforms = [
        ("Youtube", extract_youtube, youtube.validate_zip),
        ("TikTok", extract_tiktok, tiktok.validate_zip),
    ]

    # progress in %
    subflows = len(platforms)
    steps = 2
    step_percentage = (100 / subflows) / steps
    progress = 0

    # For each platform
    # 1. Prompt file extraction loop
    # 2. In case of succes render data on screen
    for platform in platforms:
        platform_name, extraction_fun, validation_fun = platform

        table_list = None
        progress += step_percentage

        # Prompt file extraction loop
        while True:
            LOGGER.info("Prompt for file for %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Render the propmt file page
            promptFile = prompt_file("application/zip, text/plain", platform_name)
            file_result = yield render_donation_page(platform_name, promptFile, progress)

            if file_result.__type__ == "PayloadString":
                validation = validation_fun(file_result.value)

                # DDP is recognized: Extraction
                if validation.ddp_category is not None:
                    LOGGER.info("Payload for %s", platform_name)
                    yield donate_logs(f"{session_id}-tracking")

                    table_list = extraction_fun(file_result.value, validation)
                    break

                # DDP is not recognized: Enter retry flow
                if validation.ddp_category is None:
                    LOGGER.info("Not a valid %s zip; No payload; prompt retry_confirmation", platform_name)
                    yield donate_logs(f"{session_id}-tracking")
                    retry_result = yield render_donation_page(platform_name, retry_confirmation(platform_name), progress)

                    if retry_result.__type__ == "PayloadTrue":
                        continue
                    else:
                        LOGGER.info("Skipped during retry %s", platform_name)
                        yield donate_logs(f"{session_id}-tracking")
                        break
            else:
                LOGGER.info("Skipped %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                break

        progress += step_percentage

        # Render data on screen
        if table_list is not None:
            LOGGER.info("Prompt consent; %s", platform_name)
            yield donate_logs(f"{session_id}-tracking")

            # Check if extract something got extracted
            if len(table_list) == 0:
                table_list.append(create_empty_table(platform_name))

            prompt = assemble_tables_into_form(table_list)
            consent_result = yield render_donation_page(platform_name, prompt, progress)

            if consent_result.__type__ == "PayloadJSON":
                LOGGER.info("Data donated; %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")
                yield donate(platform_name, consent_result.value)
            else:
                LOGGER.info("Skipped ater reviewing consent: %s", platform_name)
                yield donate_logs(f"{session_id}-tracking")

    yield render_end_page()



##################################################################

def assemble_tables_into_form(table_list: list[props.PropsUIPromptConsentFormTable]) -> props.PropsUIPromptConsentForm:
    """
    Assembles all donated data in consent form to be displayed
    """
    return props.PropsUIPromptConsentForm(table_list, [])


def create_consent_form_tables(unique_table_id: str, title: props.Translatable, df: pd.DataFrame) -> list[props.PropsUIPromptConsentFormTable]:
    """
    This function chunks extracted data into tables of 5000 rows that can be renderd on screen
    """

    df_list = helpers.split_dataframe(df, 5000)
    out = []

    if len(df_list) == 1:
        table = props.PropsUIPromptConsentFormTable(unique_table_id, title, df_list[0])
        out.append(table)
    else:
        for i, df in enumerate(df_list):
            index = i + 1
            title_with_index = props.Translatable({lang: f"{val} {index}" for lang, val in title.translations.items()})
            table = props.PropsUIPromptConsentFormTable(f"{unique_table_id}_{index}", title_with_index, df)
            out.append(table)

    return out


def donate_logs(key):
    log_string = LOG_STREAM.getvalue()  # read the log stream
    if log_string:
        log_data = log_string.split("\n")
    else:
        log_data = ["no logs"]

    return donate(key, json.dumps(log_data))


def create_empty_table(platform_name: str) -> props.PropsUIPromptConsentFormTable:
    """
    Show something in case no data was extracted
    """
    title = props.Translatable({
       "en": "Er ging niks mis, maar we konden niks vinden",
       "nl": "Er ging niks mis, maar we konden niks vinden"
    })
    df = pd.DataFrame(["No data found"], columns=["No data found"])
    table = props.PropsUIPromptConsentFormTable(f"{platform_name}_no_data_found", title, df)
    return table


##################################################################
# Extraction functions

def extract_youtube(youtube_zip: str, validation: validate.ValidateInput) -> list[props.PropsUIPromptConsentFormTable]:
    """
    Main data extraction function
    Assemble all extraction logic here
    """
    tables_to_render = []

    # Extract comments
    df = youtube.my_comments_to_df(youtube_zip, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Youtube comments", "nl": "Youtube comments"})
        tables = create_consent_form_tables("youtube_comments", table_title, df) 
        tables_to_render.extend(tables)

    # Extract Watch later.csv
    df = youtube.watch_later_to_df(youtube_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Youtube watch later", "nl": "Youtube watch later"})
        tables = create_consent_form_tables("youtube_watch_later", table_title, df) 
        tables_to_render.extend(tables)

    # Extract subscriptions.csv
    df = youtube.subscriptions_to_df(youtube_zip, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Youtube subscriptions", "nl": "Youtube subscriptions"})
        tables = create_consent_form_tables("youtube_subscriptions", table_title, df) 
        tables_to_render.extend(tables)

    # Extract subscriptions.csv
    df = youtube.watch_history_to_df(youtube_zip, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Youtube watch history", "nl": "Youtube watch history"})
        tables = create_consent_form_tables("youtube_watch_history", table_title, df) 
        tables_to_render.extend(tables)

    # Extract live chat messages
    df = youtube.my_live_chat_messages_to_df(youtube_zip, validation)
    if not df.empty:
        table_title = props.Translatable({"en": "Youtube my live chat messages", "nl": "Youtube my live chat messages"})
        tables = create_consent_form_tables("youtube_my_live_chat_messages", table_title, df) 
        tables_to_render.extend(tables)

    return tables_to_render


def extract_tiktok(tiktok_zip: str, _) -> list[props.PropsUIPromptConsentFormTable]:
    tables_to_render = []

    df = tiktok.video_browsing_history_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok video browsing history", "nl": "Tiktok video browsing history"})
        tables = create_consent_form_tables("tiktok_video_browsing_history", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.favorite_videos_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok favorite videos", "nl": "Tiktok favorite videos"})
        tables = create_consent_form_tables("tiktok_favorite_videos", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.following_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok following", "nl": "Tiktok following"})
        tables = create_consent_form_tables("tiktok_following", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.like_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok likes", "nl": "Tiktok likes"})
        tables = create_consent_form_tables("tiktok_like", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.search_history_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok search history", "nl": "Tiktok search history"})
        tables = create_consent_form_tables("tiktok_search_history", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.share_history_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok share history", "nl": "Tiktok share history"})
        tables = create_consent_form_tables("tiktok_share_history", table_title, df) 
        tables_to_render.extend(tables)

    df = tiktok.comment_to_df(tiktok_zip)
    if not df.empty:
        table_title = props.Translatable({"en": "Tiktok comment history", "nl": "Tiktok comment history"})
        tables = create_consent_form_tables("tiktok_comment", table_title, df) 
        tables_to_render.extend(tables)

    return tables_to_render



##########################################
# Functions provided by Eyra did not change

def render_end_page():
    page = props.PropsUIPageEnd()
    return CommandUIRender(page)


def render_donation_page(platform, body, progress):
    header = props.PropsUIHeader(props.Translatable({"en": platform, "nl": platform}))

    footer = props.PropsUIFooter(progress)
    page = props.PropsUIPageDonation(platform, header, body, footer)
    return CommandUIRender(page)


def retry_confirmation(platform):
    text = props.Translatable(
        {
            "en": f"Unfortunately, we could not process your {platform} file. If you are sure that you selected the correct file, press Continue. To select a different file, press Try again.",
            "nl": f"Helaas, kunnen we uw {platform} bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen."
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file(extensions, platform):
    description = props.Translatable(
        {
            "en": f"Please follow the download instructions and choose the file that you stored on your device. Click “Skip” at the right bottom, if you do not have a file from {platform}.",
            "nl": f"Volg de download instructies en kies het bestand dat u opgeslagen heeft op uw apparaat. Als u geen {platform} bestand heeft klik dan op “Overslaan” rechts onder."
        }
    )
    return props.PropsUIPromptFileInput(description, extensions)


def donate(key, json_string):
    return CommandSystemDonate(key, json_string)
