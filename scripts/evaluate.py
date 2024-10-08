import argparse
import torch
from pytorch_lightning import Trainer
from torch.utils.data import DataLoader
from tqdm import tqdm
import os
import sys
from transformers import DetrFeatureExtractor
from project_path import project_dir
from util.evaluation_support import prepare_for_evaluation
from util.coco_dataset import DetectionDataset
from model.detr import Detr, load_model_from_ckpt
from coco_eval import CocoEvaluator


def collate_fn(batch):
    feature_extractor = DetrFeatureExtractor.from_pretrained("facebook/detr-resnet-50")
    pixel_values = [item[0] for item in batch]
    encoding = feature_extractor.pad_and_create_pixel_mask(pixel_values, return_tensors="pt")
    labels = [item[1] for item in batch]
    batch = {'pixel_values': encoding['pixel_values'],
             'pixel_mask': encoding['pixel_mask'],
             'labels': labels}
    return batch


def initialize_dataloader(args):
    args.train_folder = os.path.join(args.data_dir, 'train')
    args.val_folder = os.path.join(args.data_dir, 'val')
    feature_extractor = DetrFeatureExtractor.from_pretrained("facebook/detr-resnet-50")

    train_dataset = DetectionDataset(img_folder=args.train_folder, feature_extractor=feature_extractor)
    val_dataset = DetectionDataset(img_folder=args.val_folder, feature_extractor=feature_extractor,
                                   train=False)
    print("Number of training examples:", len(train_dataset))
    print("Number of validation examples:", len(val_dataset))

    train_dataloader = DataLoader(train_dataset, collate_fn=collate_fn, batch_size=args.batch_size, num_workers=args.number_of_workers, shuffle=True)
    val_dataloader = DataLoader(val_dataset, collate_fn=collate_fn, num_workers=args.number_of_workers, batch_size=args.batch_size)
    categories = train_dataset.coco.cats
    id2label = {k: v['name'] for k, v in categories.items()}

    return train_dataloader, val_dataset, val_dataloader, feature_extractor, id2label


def initialize_model(args, train_dataloader, val_dataloader):
    return Detr(lr=args.learning_rate, lr_backbone=args.lr_backbone, weight_decay=args.weight_decay,
                train_dataloader=train_dataloader, val_dataloader=val_dataloader)


def initialize_trainer(args):
    if not torch.cuda.is_available():
        return Trainer(gpus=0, max_steps=args.max_steps, gradient_clip_val=args.gradient_clip_val,
                       default_root_dir=args.output_dir)
    else:
        return Trainer(gpus=args.gpus, max_steps=args.max_steps, gradient_clip_val=args.gradient_clip_val,
                       default_root_dir=args.output_dir, accelerator="auto")


def evaluation(model, val_dataset, val_dataloader, feature_extractor):
    iou_types = ['bbox']
    coco_evaluator = CocoEvaluator(val_dataset.coco, iou_types)
    # model
    model.eval()
    print("Running evaluation...")
    for idx, batch in enumerate(tqdm(val_dataloader)):
        # get the inputs
        pixel_values = batch["pixel_values"]
        pixel_mask = batch["pixel_mask"]
        labels = [{k: v for k, v in t.items()} for t in
                  batch["labels"]]
        # forward pass
        outputs = model.model(pixel_values=pixel_values, pixel_mask=pixel_mask)

        orig_target_sizes = torch.stack([target["orig_size"] for target in labels], dim=0)
        results = feature_extractor.post_process(outputs, orig_target_sizes)  # convert outputs of model to COCO api

        res = {target['image_id'].item(): output for target, output in zip(labels, results)}
        res = prepare_for_evaluation(res)
        coco_evaluator.update(res)

    coco_evaluator.synchronize_between_processes()
    coco_evaluator.accumulate()
    coco_evaluator.summarize()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str, required=True,
                        help="path to the directory that contains the split data.")
    parser.add_argument('--batch_size', type=str, default=4)
    parser.add_argument('--gpus', type=int, default=1)
    parser.add_argument('--number_of_workers', type=int, default=4)
    parser.add_argument('--checkpoint_path', type=str, required=True,
                        help="Path to the checkpoint.")
    args = parser.parse_args()
    _, val_dataset, val_dataloader, feature_extractor, _ = initialize_dataloader(args)

    model = load_model_from_ckpt(args)

    evaluation(model, val_dataset, val_dataloader, feature_extractor)
