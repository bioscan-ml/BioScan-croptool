import argparse
import os
import torch
import torchvision
from pytorch_lightning import Trainer
from torch.utils.data import DataLoader
from tqdm import tqdm
from transformers import DetrFeatureExtractor
from os.path import dirname, abspath
import sys
project_dir = dirname(dirname(abspath(__file__)))
sys.path.append(project_dir)
from util.coco_relevent import CocoDetection
from model.detr import Detr
from coco_eval import CocoEvaluator




def collate_fn(batch):
    feature_extractor = DetrFeatureExtractor.from_pretrained("facebook/detr-resnet-50")
    pixel_values = [item[0] for item in batch]
    encoding = feature_extractor.pad_and_create_pixel_mask(pixel_values, return_tensors="pt")
    labels = [item[1] for item in batch]
    batch = {}
    batch['pixel_values'] = encoding['pixel_values']
    batch['pixel_mask'] = encoding['pixel_mask']
    batch['labels'] = labels
    return batch


def create_dataloader(args):
    args.train_folder = os.path.join(args.data_dir, 'train')
    args.val_folder = os.path.join(args.data_dir, 'val')
    feature_extractor = DetrFeatureExtractor.from_pretrained("facebook/detr-resnet-50")

    train_dataset = CocoDetection(img_folder=args.train_folder, feature_extractor=feature_extractor)
    val_dataset = CocoDetection(img_folder=args.val_folder, feature_extractor=feature_extractor,
                                train=False)
    print("Number of training examples:", len(train_dataset))
    print("Number of validation examples:", len(val_dataset))

    train_dataloader = DataLoader(train_dataset, collate_fn=collate_fn, batch_size=args.batch_size, shuffle=True)
    val_dataloader = DataLoader(val_dataset, collate_fn=collate_fn, batch_size=args.batch_size)
    cats = train_dataset.coco.cats
    id2label = {k: v['name'] for k, v in cats.items()}

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

def convert_to_xywh(boxes):
    xmin, ymin, xmax, ymax = boxes.unbind(1)
    return torch.stack((xmin, ymin, xmax - xmin, ymax - ymin), dim=1)

def prepare_for_coco_detection(predictions):
    coco_results = []
    for original_id, prediction in predictions.items():
        if len(prediction) == 0:
            continue
        boxes = prediction["boxes"]
        boxes = convert_to_xywh(boxes).tolist()
        scores = prediction["scores"].tolist()
        labels = prediction["labels"].tolist()
        coco_results.extend(
            [
                {
                    "image_id": original_id,
                    "category_id": labels[k],
                    "bbox": box,
                    "score": scores[k],
                }
                for k, box in enumerate(boxes)
            ]
        )
    return coco_results

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
                  batch["labels"]]  # these are in DETR format, resized + normalized

        # forward pass
        outputs = model.model(pixel_values=pixel_values, pixel_mask=pixel_mask)

        orig_target_sizes = torch.stack([target["orig_size"] for target in labels], dim=0)
        results = feature_extractor.post_process(outputs, orig_target_sizes)  # convert outputs of model to COCO api

        res = {target['image_id'].item(): output for target, output in zip(labels, results)}
        res = prepare_for_coco_detection(res)
        coco_evaluator.update(res)

    coco_evaluator.synchronize_between_processes()
    coco_evaluator.accumulate()
    coco_evaluator.summarize()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str, required=True,
                        help="path to the directory that contains the split data")
    parser.add_argument('--batch_size', type=str, default=2)
    parser.add_argument('--learning_rate', type=float, default=1e-4)
    parser.add_argument('--lr_backbone', type=float, default=1e-5)
    parser.add_argument('--weight_decay', type=float, default=1e-4)
    parser.add_argument('--gpus', type=int, default=1)
    parser.add_argument('--max_steps', type=int, default=300)
    parser.add_argument('--gradient_clip_val', type=float, default=0.1)
    parser.add_argument('--output_dir', type=str, required=True, help="The path used to store the checkpoint")
    args = parser.parse_args()
    train_dataloader, val_dataset, val_dataloader, feature_extractor, id2label = create_dataloader(args)

    model = initialize_model(args, train_dataloader, val_dataloader)

    trainer = initialize_trainer(args)

    trainer.fit(model)

    evaluation(model, val_dataset, val_dataloader, feature_extractor)
