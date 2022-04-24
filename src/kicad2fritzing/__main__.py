import logging


logger = None






if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    logger.debug(f'begin')   
    logger.debug(f'end')   



